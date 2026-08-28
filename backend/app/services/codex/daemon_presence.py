"""Cross-container presence + job routing for codex daemons (spec §4 Redis half).

Why this exists: the daemon's WebSocket lives in the *gateway* container's
process memory, but canvas generation workflows execute in the *worker*
container. A process-local registry answers "is the user's daemon online?"
correctly only in the process that holds the socket — everywhere else it
would say "offline" forever. Redis is the shared truth:

- ``codex_online:<user_id>`` — presence marker whose TTL is refreshed by
  real heartbeats only. A zombie socket (no FIN, no pings) stops refreshing
  and the marker dies ≤90s later — the marker cannot lie for long, which is
  the falsifiability rule healthchecks must obey.
- ``codex_jobs:<user_id>`` — worker → gateway job hand-off channel.
- ``codex_results:<job_id>`` — gateway → worker result hand-off channel.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from app.core.redis import get_async_redis

ONLINE_KEY_PREFIX = "codex_online:"
JOBS_CHANNEL_PREFIX = "codex_jobs:"
RESULTS_CHANNEL_PREFIX = "codex_results:"

# Heartbeats land every 30s; three misses ⇒ gone. Same 90s the frontend badge
# uses — one number, three consumers, zero drift.
ONLINE_TTL_SECONDS = 90


async def mark_online(user_id: str, device_id: str) -> None:
    redis = await get_async_redis()
    await redis.set(f"{ONLINE_KEY_PREFIX}{user_id}", device_id, ex=ONLINE_TTL_SECONDS)


async def mark_offline(user_id: str, device_id: str) -> None:
    redis = await get_async_redis()
    await redis.delete(f"{ONLINE_KEY_PREFIX}{user_id}")


async def is_online_anywhere(user_id: str) -> bool:
    """Presence check that works from ANY container, not just the socket's."""
    try:
        redis = await get_async_redis()
        return bool(await redis.exists(f"{ONLINE_KEY_PREFIX}{user_id}"))
    except Exception as exc:
        logger.warning("[codex-daemon] presence check failed: {}", exc)
        return False


async def online_device_id(user_id: str) -> Optional[str]:
    """Which device is holding this user's socket right now, if any.

    The presence key's VALUE is the device id (see ``mark_online``), so this
    is the same round trip ``is_online_anywhere`` already makes — it just
    keeps the answer instead of throwing it away.

    ``None`` means "nothing connected, or we could not ask". Callers must not
    read that as a verdict about the device: answering "your daemon is out of
    date" when the real problem is that no daemon is running sends the user to
    update something that is not even started.
    """
    try:
        redis = await get_async_redis()
        raw = await redis.get(f"{ONLINE_KEY_PREFIX}{user_id}")
    except Exception as exc:
        logger.warning("[codex-daemon] presence lookup failed: {}", exc)
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def publish_job(user_id: str, job: dict[str, Any]) -> None:
    redis = await get_async_redis()
    await redis.publish(f"{JOBS_CHANNEL_PREFIX}{user_id}", json.dumps(job))


async def publish_result(job_id: str, result: dict[str, Any]) -> None:
    redis = await get_async_redis()
    await redis.publish(f"{RESULTS_CHANNEL_PREFIX}{job_id}", json.dumps(result))


async def claim_job(job_id: str, device_id: str) -> bool:
    """First-wins claim so a user with TWO connected devices runs a job once.

    Every gateway process forwarding for the same user receives the pub/sub
    message; only the SET NX winner actually hands it to its daemon.
    """
    redis = await get_async_redis()
    ok = await redis.set(f"codex_job_claim:{job_id}", device_id, nx=True, ex=900)
    return bool(ok)
