"""Dispatch a canvas generation to the user's OWN daemon (C4, spec §5).

Cross-container by construction: the daemon's socket lives in the *gateway*
process while generation workflows run in the *worker* container, so every
leg rides Redis —

    worker ──publish codex_jobs:<user>──► gateway (socket holder) ──WS──► daemon
    worker ◄─subscribe codex_results:<job_id>── gateway ◄──WS── daemon

Two rules shape this module:

1. **Offline is an answer, not a wait.** No live presence marker → typed
   ``DaemonOfflineError`` at click time, never a silent hang.
2. **A daemon that dies mid-job never publishes a result** — hence the hard
   timeout on the results subscription.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Optional, Protocol

from loguru import logger

DEFAULT_TIMEOUT_S = 600


class DaemonOfflineError(RuntimeError):
    """The user has no daemon connected right now."""


class DaemonTransport(Protocol):
    async def is_online(self, user_id: str) -> bool: ...
    async def send_job(self, user_id: str, job: dict) -> None: ...
    async def wait_result(self, job_id: str, timeout_s: float) -> dict: ...


class RedisDaemonTransport:
    """Production transport: presence + pub/sub via the shared Redis."""

    async def is_online(self, user_id: str) -> bool:
        from app.services.codex import daemon_presence

        return await daemon_presence.is_online_anywhere(user_id)

    async def send_job(self, user_id: str, job: dict) -> None:
        from app.services.codex import daemon_presence

        await daemon_presence.publish_job(user_id, job)

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        from app.core.redis import get_async_redis
        from app.services.codex.daemon_presence import RESULTS_CHANNEL_PREFIX

        redis = await get_async_redis()
        pubsub = redis.pubsub()
        channel = f"{RESULTS_CHANNEL_PREFIX}{job_id}"
        await pubsub.subscribe(channel)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=min(remaining, 5.0),
                )
                if message is None:
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    return json.loads(data)
                except Exception:
                    continue
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass


async def dispatch_to_daemon(
    *,
    user_id: str,
    scope_id: int,
    kind: str,
    payload: dict[str, Any],
    transport: Optional[DaemonTransport] = None,
    mint_ticket: Optional[Callable[..., Any]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send one job to the user's daemon and await its result.

    Raises ``DaemonOfflineError`` when nothing is connected, ``TimeoutError``
    when the daemon never answers, ``RuntimeError`` when it answers with a
    failure (message carries the daemon's typed code).
    """
    if transport is None:
        transport = RedisDaemonTransport()
    if mint_ticket is None:
        from app.api.codex_daemon_router import mint_upload_ticket

        mint_ticket = mint_upload_ticket

    if not await transport.is_online(user_id):
        raise DaemonOfflineError(
            "your local codex daemon is not connected — run `nous-codex run`"
        )

    job_id = str(uuid.uuid4())
    ticket = mint_ticket(user_id=user_id, scope_id=scope_id, job_id=job_id)
    if asyncio.iscoroutine(ticket):
        ticket = await ticket

    job = {
        "type": "job",
        "job_id": job_id,
        "kind": kind,
        "payload": {**payload, "upload_ticket": ticket},
    }
    # The waiter owns its own subscription; start it BEFORE publishing so the
    # daemon's (fast) answer can never race past an unsubscribed channel.
    waiter = asyncio.create_task(transport.wait_result(job_id, timeout_s))
    await asyncio.sleep(0)
    try:
        await transport.send_job(user_id, job)
        result = await waiter
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"local codex daemon did not answer within {int(timeout_s)}s"
        ) from exc
    finally:
        if not waiter.done():
            waiter.cancel()

    if result.get("error"):
        logger.info("[codex-daemon] job {} failed: {}", job_id, result["error"])
        raise RuntimeError(str(result["error"]))
    return result
