"""codex_daemon_ws_router — the daemon's outbound WebSocket (C2).

The daemon dials OUT to nous (no inbound port, no public IP needed on the
user's machine) and holds the socket open. Auth is the device token minted
at pairing: we hash it and look the device up; unknown or revoked tokens are
refused BEFORE accept() so a dead device never sees an open socket.

Protocol (spec §4/§5):
  daemon → {"type":"ping"}                  every 30s
  server → {"type":"pong"}
  server → {"type":"job", ...}              dispatch (C4)
  daemon → {"type":"job_done"|"job_failed"} result (C4)
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api.codex_daemon_router import token_hash
from app.services.codex.daemon_dispatch import resolve_job
from app.services.codex.daemon_registry import registry

router = APIRouter(tags=["Codex Daemon"])

# 90s without a ping (3 missed heartbeats) ⇒ the device is gone.
HEARTBEAT_TIMEOUT_SECONDS = 90


async def _lookup_device(hashed: str) -> Optional[dict[str, Any]]:
    from app.repositories.codex_daemon_repository import CodexDaemonRepository

    return await CodexDaemonRepository().find_by_token_hash(hashed)


async def authenticate_device(device_token: str) -> Optional[dict[str, Any]]:
    """Device row for this token, or None when unknown/revoked/empty."""
    if not device_token:
        return None
    return await _lookup_device(token_hash(device_token))


@router.websocket("/ws/codex-agent")
async def ws_codex_agent(websocket: WebSocket) -> None:
    # The daemon is NOT a browser, so it can send a real Authorization
    # header — the token never lands in a URL / access log (the lesson the
    # ?token= path in ws_router documents).
    header = websocket.headers.get("authorization", "")
    device_token = header[7:] if header.lower().startswith("bearer ") else ""
    device = await authenticate_device(device_token)
    if not device:
        await websocket.close(code=4001, reason="device authentication failed")
        return

    user_id = str(device["user_id"])
    device_id = str(device["id"])
    await websocket.accept()
    registry.register(user_id=user_id, device_id=device_id, ws=websocket)
    await _touch_last_seen(device_id)

    try:
        while True:
            message = await websocket.receive_json()
            kind = str(message.get("type") or "")
            if kind == "ping":
                await websocket.send_json({"type": "pong"})
                await _touch_last_seen(device_id)
            elif kind in ("job_done", "job_failed", "job_progress"):
                job_id = str(message.get("job_id") or "")
                logger.info(
                    "[codex-daemon] {} from device={} job={}", kind, device_id, job_id
                )
                if kind == "job_done":
                    resolve_job(
                        job_id,
                        {
                            "gen_id": message.get("gen_id"),
                            "text": message.get("text"),
                        },
                    )
                elif kind == "job_failed":
                    code = str(message.get("code") or "job_failed")
                    resolve_job(
                        job_id,
                        {"error": f"{code}: {message.get('message') or ''}".strip()},
                    )
            else:
                logger.debug("[codex-daemon] unknown frame: {}", kind)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[codex-daemon] socket error device={} err={}", device_id, exc)
    finally:
        registry.unregister(user_id=user_id, device_id=device_id)


async def _touch_last_seen(device_id: str) -> None:
    try:
        from app.repositories.codex_daemon_repository import CodexDaemonRepository

        await CodexDaemonRepository().touch_last_seen(int(device_id))
    except Exception as exc:  # heartbeat bookkeeping must never kill the socket
        logger.debug("[codex-daemon] last_seen update failed: {}", exc)
