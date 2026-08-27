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
            elif kind in ("job_done", "job_failed", "job_progress"):
                job_id = str(message.get("job_id") or "")
                logger.info(
                    "[codex-daemon] {} from device={} job={}", kind, device_id, job_id
                )
                if kind == "job_done":
                    await daemon_presence.publish_result(
                        job_id,
                        {
                            "gen_id": message.get("gen_id"),
                            "text": message.get("text"),
                        },
                    )
                elif kind == "job_failed":
                    code = str(message.get("code") or "job_failed")
                    await daemon_presence.publish_result(
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
        forwarder.cancel()
        registry.unregister(user_id=user_id, device_id=device_id)
        # Clearing presence on one device's exit briefly hides a second
        # device of the same user; its next ping (≤30s) restores the marker.
        # Better a 30s false-offline than a 90s zombie-online.
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
