"""codex_daemon_router — pairing + registry for per-user codex daemons.

C 方案 (design:
``docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md``):
each user runs a small daemon on their OWN machine, holding their OWN codex
login. nous never sees those credentials — it only knows a device exists and
keeps a token HASH to authenticate that device's outbound WebSocket.

Pairing mirrors ``ws_ticket_router``'s proven one-shot-code pattern (Redis
GETDEL, no replay):

1. Browser POSTs ``/codex-daemon/pair-code`` → 8-char code, TTL 10 min.
2. User runs ``nous-codex pair <code>`` on their machine.
3. Daemon POSTs ``/codex-daemon/pair`` → device token (plaintext returned
   ONCE, only its sha256 is stored) + device row.

The plaintext token then authenticates the daemon's WS connection.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.core.redis import get_async_redis

router = APIRouter(prefix="/codex-daemon", tags=["Codex Daemon"])

PAIR_KEY_PREFIX = "codex_pair:"
UPLOAD_KEY_PREFIX = "codex_upload:"
UPLOAD_TTL_SECONDS = 1800
PAIR_TTL_SECONDS = 600
# Ambiguous glyphs (0/O, 1/I/L) are out — the code is read off a screen and
# typed into a terminal by hand.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _mint_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── seams (patched in tests) ──────────────────────────────────────────────


async def _store_pair_code(code: str, user_id: str) -> None:
    redis = await get_async_redis()
    await redis.set(f"{PAIR_KEY_PREFIX}{code}", user_id, ex=PAIR_TTL_SECONDS)


async def _consume_pair_code(code: str) -> Optional[str]:
    """Atomic GETDEL — a code pairs exactly one device."""
    if not code:
        return None
    redis = await get_async_redis()
    try:
        raw = await redis.execute_command("GETDEL", f"{PAIR_KEY_PREFIX}{code}")
    except Exception as exc:
        logger.exception(f"[codex-daemon] pair-code consume failed: {exc}")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return str(raw) or None


async def _insert_daemon(**kwargs: Any) -> dict:
    from app.repositories.codex_daemon_repository import CodexDaemonRepository

    return await CodexDaemonRepository().create(**kwargs)


async def _list_daemons(user_id: str) -> list[dict]:
    from app.repositories.codex_daemon_repository import CodexDaemonRepository

    return await CodexDaemonRepository().list_for_user(user_id)


async def _revoke_daemon(user_id: str, device_id: int) -> bool:
    from app.repositories.codex_daemon_repository import CodexDaemonRepository

    return await CodexDaemonRepository().revoke(user_id=user_id, device_id=device_id)


# ── schemas ───────────────────────────────────────────────────────────────


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    device_name: str = Field(min_length=1, max_length=64)
    platform: str = Field(default="", max_length=32)


# ── routes ────────────────────────────────────────────────────────────────


@router.post("/pair-code")
async def issue_pair_code(auth: AuthDep) -> dict:
    """Mint a one-shot pairing code for the signed-in user (TTL 10 min)."""
    code = _mint_code()
    try:
        await _store_pair_code(code, str(auth.user_id))
    except Exception as exc:
        logger.exception(f"[codex-daemon] pair-code mint failed: {exc}")
        raise HTTPException(500, "pair code store unavailable")
    return {"data": {"code": code, "expires_in_seconds": PAIR_TTL_SECONDS}}


@router.post("/pair")
async def pair_device(payload: PairRequest) -> dict:
    """Swap a pairing code for a device token.

    Deliberately UNAUTHENTICATED: the daemon has no nous session — the code
    IS the proof of the user's intent, and it is one-shot + short-lived.
    """
    user_id = await _consume_pair_code(payload.code.strip().upper())
    if not user_id:
        raise HTTPException(400, "pairing code invalid or already used")
    device_token = secrets.token_urlsafe(32)
    row = await _insert_daemon(
        user_id=user_id,
        device_name=payload.device_name,
        platform=payload.platform,
        token_hash=token_hash(device_token),
    )
    device_id = row.get("id")
    if device_id is None:
        raise HTTPException(500, "device registration failed")
    # Plaintext token travels exactly once, in this response.
    return {"data": {"device_id": str(device_id), "device_token": device_token}}


async def mint_upload_ticket(*, user_id: str, scope_id: int, job_id: str) -> str:
    """One-shot upload authority handed to the daemon inside a job (spec §5).

    The daemon has no nous session; this ticket is its only credential, so it
    is short-lived, single-use and carries the owner it will file under.
    """
    ticket = secrets.token_urlsafe(32)
    redis = await get_async_redis()
    await redis.set(
        f"{UPLOAD_KEY_PREFIX}{ticket}",
        json.dumps({"user_id": user_id, "scope_id": scope_id, "job_id": job_id}),
        ex=UPLOAD_TTL_SECONDS,
    )
    return ticket


async def _consume_upload_ticket(ticket: str) -> Optional[dict]:
    if not ticket:
        return None
    redis = await get_async_redis()
    try:
        raw = await redis.execute_command("GETDEL", f"{UPLOAD_KEY_PREFIX}{ticket}")
    except Exception as exc:
        logger.exception(f"[codex-daemon] upload ticket consume failed: {exc}")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _register_daemon_result(**kwargs: Any) -> dict:
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        register_generated_media,
    )

    params = kwargs.pop("origin_params", {})
    return await register_generated_media(
        origin=GenerationOrigin(kind="canvas_upload", params=params), **kwargs
    )


@router.post("/upload")
async def upload_daemon_result(
    file: UploadFile = File(...),
    ticket: str = Form(...),
) -> dict:
    """Receive a daemon-produced file and file it under the ticket's owner."""
    claim = await _consume_upload_ticket(ticket)
    if not claim:
        raise HTTPException(401, "upload ticket invalid or already used")
    mime = (file.content_type or "image/png").lower()
    if not (mime.startswith("image/") or mime.startswith("video/")):
        raise HTTPException(400, "only image/* or video/* uploads")
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="codex_daemon_")
        total = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 50 * 1024 * 1024:
                    raise HTTPException(413, "file exceeds 50MB")
                out.write(chunk)
        if total == 0:
            raise HTTPException(400, "empty file")
        row = await _register_daemon_result(
            user_id=str(claim["user_id"]),
            scope_id=int(claim["scope_id"]),
            source_path=tmp_path,
            mime=mime,
            origin_params={
                "produced_by": "codex-daemon",
                "job_id": claim.get("job_id"),
            },
        )
        gen_id = row.get("id")
        if gen_id is None:
            raise HTTPException(500, "registration failed")
        return {"data": {"gen_id": str(gen_id)}}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.get("/devices")
async def list_devices(auth: AuthDep) -> dict:
    rows = await _list_daemons(str(auth.user_id))
    return {"data": rows}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: int, auth: AuthDep) -> dict:
    ok = await _revoke_daemon(str(auth.user_id), device_id)
    if not ok:
        raise HTTPException(404, "device not found")
    # Revocation must be immediate — a still-open socket would keep taking
    # jobs after the user pulled the device (spec §10 security checklist).
    from app.services.codex.daemon_registry import registry

    await registry.disconnect_device(str(auth.user_id), str(device_id))
    return {"data": {"revoked": True}}
