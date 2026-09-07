"""codex_daemon_ws_router — the daemon's outbound WebSocket (C2 + C4 glue).

The daemon dials OUT to nous (no inbound port, no public IP needed on the
user's machine) and holds the socket open. Auth is the device token minted
at pairing: we hash it and look the device up. Unknown or revoked tokens are
refused by accepting the handshake and then closing with 4001 — NOT by
closing before accept(). Closing an unaccepted WebSocket makes uvicorn (all
three of its ws implementations) reject the HTTP handshake with a 403, which
throws the close code away: the client only ever sees 1006, which it must
treat as a transient network failure and retry forever. Accepting first is
what lets the daemon distinguish "revoked, stop for good" from "the wifi
blinked". Costs one accepted socket that is closed microseconds later.

Cross-container glue (spec §4): this handler runs in the *gateway* process,
but jobs originate in the *worker* container. So on connect we

- refresh the Redis presence marker on every real heartbeat (a zombie
  socket stops refreshing and the marker dies with it — presence stays
  falsifiable), and
- run a forwarder task subscribed to ``codex_jobs:<user_id>`` that hands
  each job to this socket (first-wins claim dedupes multi-device users),
  publishing the daemon's verdict back on ``codex_results:<job_id>``.

Protocol (spec §4/§5):
  daemon → {"type":"ping"}                  every 30s
  server → {"type":"pong"}
  server → {"type":"job", ...}              dispatch
  daemon → {"type":"job_chunk", ...}        one slice of an oversized text
  daemon → {"type":"job_done"|"job_failed"} result
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api.codex_daemon_router import token_hash
from app.services.codex import daemon_presence
from app.services.codex.daemon_registry import registry

router = APIRouter(tags=["Codex Daemon"])

# 90s without a frame (3 missed heartbeats) ⇒ the device is gone. Enforced
# via receive timeout — a half-dead TCP session cannot squat the registry.
HEARTBEAT_TIMEOUT_SECONDS = 90


# Chunk-buffer bounds. The daemon slices at 256 KiB (index.mjs::chunkText)
# and only chunks above a 900 KiB inline limit, so a real text job needs a
# handful of frames; 64 × 256 KiB = MAX_TEXT_BYTES is the ceiling we accept.
# These are NOT tuning knobs — they are what keeps a malformed frame from
# turning into an unbounded allocation inside the *gateway* process, which
# holds every user's daemon socket (see the module docstring).
MAX_TEXT_CHUNKS = 64
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_BUFFERED_JOBS = 8


class _Poisoned:
    """Marker for a job whose chunk stream is unusable.

    Kept as the job's value (not deleted) so later frames of the same job are
    ignored instead of starting a fresh buffer, and so job_done can publish a
    typed failure instead of a plausible-looking short text. Popped like any
    other buffer. Carries `reason` so the published error says WHICH frame
    invariant broke — the daemon and this backend version-skew independently,
    and "invalid chunk frame" alone cannot tell a truncated stream from a
    daemon that started numbering at 1.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


def take_chunk(chunks: dict[str, list[str] | _Poisoned], message: dict) -> None:
    """Buffer one job_chunk frame.

    Every field here comes off the wire from a daemon that runs on the user's
    machine, is upgraded independently of this backend, and is therefore
    untrusted input (the same stance ``handle_env_report`` takes). A malformed
    frame must never raise: an exception escapes the receive loop, kills the
    socket, and leaves the waiting job with *zero* published results — a 600s
    hang instead of an error. So bad frames poison the job's buffer, and
    ``assemble_job_result`` turns that into one typed chunk_mismatch.
    """
    job_id = str(message.get("job_id") or "")
    if not job_id:
        logger.debug("[codex-daemon] dropping job_chunk without job_id")
        return
    buf = chunks.get(job_id, [])
    if isinstance(buf, _Poisoned):
        return  # already unusable; ignore the rest of this job's stream
    if job_id not in chunks and len(chunks) >= MAX_BUFFERED_JOBS:
        # Refuse to open a 9th buffer rather than recording a poison key —
        # that keeps the per-socket key count hard-bounded. The job still
        # gets a typed failure: its job_done hits the count guard below.
        logger.debug("[codex-daemon] too many buffered jobs; dropping chunk")
        return

    def poison(reason: str) -> None:
        logger.debug("[codex-daemon] poisoning job={} — {}", job_id, reason)
        chunks[job_id] = _Poisoned(reason)

    seq = message.get("seq")
    data = message.get("data")
    # bool is an int subclass — `True` must not be read as seq 1.
    if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(data, str):
        return poison("malformed chunk frame")
    if not 0 <= seq < MAX_TEXT_CHUNKS:
        return poison(f"seq {seq} out of range")
    if seq < len(buf) and buf[seq] != "":
        return poison(f"duplicate seq {seq}")
    try:
        incoming = len(data.encode("utf-8"))
    except UnicodeEncodeError:
        # A lone surrogate ('\ud800') is legal JSON and json.loads hands it
        # back as a str, but it has no UTF-8 encoding. Letting this raise
        # would kill the socket and publish nothing — the exact 600s hang
        # every other guard here exists to prevent.
        return poison("undecodable chunk data")
    if _buffered_bytes(buf) + incoming > MAX_TEXT_BYTES:
        return poison("text over byte cap")

    chunks[job_id] = buf
    while len(buf) <= seq:
        buf.append("")
    buf[seq] = data


def _buffered_bytes(buf: list[str]) -> int:
    """UTF-8 size of what is buffered so far. Re-encodes each frame, which is
    bounded by MAX_TEXT_BYTES × MAX_TEXT_CHUNKS of work per job — cheap at the
    real chunk counts (a handful) and self-limiting at the adversarial one.

    ``surrogatepass`` is belt-and-braces: take_chunk already refuses data that
    has no UTF-8 encoding, so nothing here should contain a lone surrogate.
    It is here so that reordering the guards can never turn this sum — which
    runs on every frame — back into a socket-killing raise.
    """
    return sum(len(p.encode("utf-8", "surrogatepass")) for p in buf)


# How much of the model's prose crosses this seam. It goes on to a jsonb
# column and then into a UI panel; the useful part (why, plus the rewrite the
# model offers) is a short paragraph. The daemon already caps it — this is the
# server refusing to take the daemon's word for it.
MODEL_DETAIL_MAX = 1500


def assemble_job_failure(message: dict) -> dict:
    """Turn a ``job_failed`` frame into the pub/sub result.

    ``error`` keeps the exact ``"<code>: <message>"`` shape
    ``codex.errors.from_daemon_error`` splits on — changing it would collapse
    every typed codex-local failure into the generic ``codex_failed``.

    ``error_detail`` is new in daemon 0.5.0 and carries the MODEL's own words
    for a content refusal (why it declined, and the rewrite it suggests). It
    is payload to show a human, never a signal: nothing branches on it, and
    ``code`` remains the only verdict. A 0.4.0 daemon sends no ``detail``, and
    absent must stay absent — an empty string here would render as a blank
    explanation panel, which reads as "the model said nothing" rather than
    "this daemon cannot tell you".

    The frame arrives off a socket, so a non-string ``detail`` is dropped
    rather than coerced: shape is not a promise.
    """
    code = str(message.get("code") or "job_failed")
    out: dict = {"error": f"{code}: {message.get('message') or ''}".strip()}
    detail = message.get("detail")
    if isinstance(detail, str) and detail.strip():
        out["error_detail"] = detail[:MODEL_DETAIL_MAX]
    return out


def assemble_job_result(
    message: dict, chunks: dict[str, list[str] | _Poisoned]
) -> dict:
    """Turn a job_done frame (+ any buffered chunks) into the pub/sub result.

    Pops this job's buffer on every path — success, mismatch and poison alike
    — so a failed job leaves nothing behind. That is *not* a no-leak
    guarantee for the dict as a whole: a daemon that streams chunks and then
    dies sends neither job_done nor job_failed, so its buffer survives until
    the socket handler exits and the whole dict goes with it. MAX_BUFFERED_JOBS
    is what bounds that window.
    """
    job_id = str(message.get("job_id") or "")
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    marker = chunks.get(job_id)
    if isinstance(marker, _Poisoned):
        chunks.pop(job_id, None)
        return {"error": f"chunk_mismatch: invalid chunk frame ({marker.reason})"}
    if message.get("chunked"):
        parts = chunks.pop(job_id, []) or []
        try:
            expected = int(message.get("chunks"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            expected = -1
        if expected <= 0:
            # Without this, a truncated/version-skewed frame (no `chunks`
            # field ⇒ expected 0, parts []) satisfies BOTH guards below and
            # publishes text:"" as a success. Empty output is not a result.
            return {"error": f"chunk_mismatch: chunked frame declared {expected}"}
        # The two guards close different holes and neither subsumes the other:
        # the count check alone is fooled by a hole padded to the right length
        # (seq 0,2 with chunks=3), the hole check alone is fooled by a chunk
        # that was never sent at all (seq 0,1 with chunks=3).
        if expected != len(parts) or any(p == "" for p in parts):
            return {"error": f"chunk_mismatch: expected {expected}, got {len(parts)}"}
        # gen_id is None by construction: only text jobs ever chunk (image and
        # dreamina jobs answer with a single job_done carrying gen_id).
        return {"gen_id": None, "text": "".join(parts), "usage": usage}
    chunks.pop(job_id, None)
    return {
        "gen_id": message.get("gen_id"),
        "text": message.get("text"),
        "usage": usage,
    }


async def _save_env_report(device_id: str, report: dict) -> None:
    from app.repositories.codex_daemon_repository import CodexDaemonRepository

    await CodexDaemonRepository().save_env_report(int(device_id), report)


async def handle_env_report(device_id: str, message: dict) -> None:
    """Persist a daemon environment self-check; malformed frames are noise,
    never an error (the socket must survive a buggy daemon build)."""
    report = message.get("report")
    if not isinstance(report, dict):
        return
    try:
        await _save_env_report(device_id, report)
    except Exception as exc:
        logger.debug("[codex-daemon] env report save failed: {}", exc)


async def _lookup_device(hashed: str) -> Optional[dict[str, Any]]:
    from app.repositories.codex_daemon_repository import CodexDaemonRepository

    return await CodexDaemonRepository().find_by_token_hash(hashed)


async def authenticate_device(device_token: str) -> Optional[dict[str, Any]]:
    """Device row for this token, or None when unknown/revoked/empty."""
    if not device_token:
        return None
    return await _lookup_device(token_hash(device_token))


async def _forward_jobs(websocket: WebSocket, user_id: str, device_id: str) -> None:
    """Relay worker-published jobs from Redis to this device's socket."""
    from app.core.redis import get_async_redis

    redis = await get_async_redis()
    pubsub = redis.pubsub()
    channel = f"{daemon_presence.JOBS_CHANNEL_PREFIX}{user_id}"
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=5.0
            )
            if message is None:
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            try:
                job = json.loads(data)
            except Exception:
                continue
            job_id = str(job.get("job_id") or "")
            if not job_id:
                continue
            if not await daemon_presence.claim_job(job_id, device_id):
                continue  # another device of this user won the job
            try:
                await websocket.send_json(job)
            except Exception as exc:
                # Socket died between claim and send: publish a typed failure
                # so the worker's waiter fails fast instead of timing out.
                logger.warning(
                    "[codex-daemon] forward failed device={} err={}", device_id, exc
                )
                await daemon_presence.publish_result(
                    job_id, {"error": "daemon_offline: socket closed mid-dispatch"}
                )
                return
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            pass


@router.websocket("/ws/codex-agent")
async def ws_codex_agent(websocket: WebSocket) -> None:
    # The daemon is NOT a browser, so it can send a real Authorization
    # header — the token never lands in a URL / access log (the lesson the
    # ?token= path in ws_router documents).
    header = websocket.headers.get("authorization", "")
    device_token = header[7:] if header.lower().startswith("bearer ") else ""
    device = await authenticate_device(device_token)
    if not device:
        # accept() first — see the module docstring. A close() before accept()
        # degrades to an HTTP 403 and the 4001 never reaches the daemon, which
        # then reconnect-loops a revoked device forever. Note a revoked device
        # reconnecting lands HERE, not on the 4003 path: _lookup_device
        # filters revoked_at IS NULL, so its token no longer resolves at all.
        await websocket.accept()
        await websocket.close(code=4001, reason="device authentication failed")
        return

    user_id = str(device["user_id"])
    device_id = str(device["id"])
    await websocket.accept()
    registry.register(user_id=user_id, device_id=device_id, ws=websocket)
    await daemon_presence.mark_online(user_id, device_id)
    await _touch_last_seen(device_id)
    forwarder = asyncio.create_task(_forward_jobs(websocket, user_id, device_id))
    chunks: dict[str, list[str] | _Poisoned] = {}

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=HEARTBEAT_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.info(
                    "[codex-daemon] heartbeat timeout device={} — closing", device_id
                )
                break
            kind = str(message.get("type") or "")
            if kind == "ping":
                await websocket.send_json({"type": "pong"})
                await daemon_presence.mark_online(user_id, device_id)
                await _touch_last_seen(device_id)
            elif kind == "env_report":
                await handle_env_report(device_id, message)
            elif kind == "job_chunk":
                take_chunk(chunks, message)
            elif kind in ("job_done", "job_failed", "job_progress"):
                job_id = str(message.get("job_id") or "")
                logger.info(
                    "[codex-daemon] {} from device={} job={}", kind, device_id, job_id
                )
                if kind == "job_done":
                    await daemon_presence.publish_result(
                        job_id, assemble_job_result(message, chunks)
                    )
                elif kind == "job_failed":
                    chunks.pop(job_id, None)
                    await daemon_presence.publish_result(
                        job_id, assemble_job_failure(message)
                    )
            else:
                logger.debug("[codex-daemon] unknown frame: {}", kind)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[codex-daemon] socket error device={} err={}", device_id, exc)
    finally:
        forwarder.cancel()
        # Only THIS socket's registration is torn down: a reconnect that beat
        # us here re-registered the same device with a fresh socket, and its
        # presence must survive our exit (2026-09-07). Clearing presence on
        # one device's exit still briefly hides a second device of the same
        # user; its next ping (≤30s) restores the marker. Better a 30s
        # false-offline than a 90s zombie-online.
        if registry.unregister(user_id=user_id, device_id=device_id, ws=websocket):
            await daemon_presence.mark_offline(user_id, device_id)
        try:
            await websocket.close()
        except Exception:
            pass


async def _touch_last_seen(device_id: str) -> None:
    try:
        from app.repositories.codex_daemon_repository import CodexDaemonRepository

        await CodexDaemonRepository().touch_last_seen(int(device_id))
    except Exception as exc:  # heartbeat bookkeeping must never kill the socket
        logger.debug("[codex-daemon] last_seen update failed: {}", exc)
