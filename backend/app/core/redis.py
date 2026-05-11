# app/core/redis.py

"""
Redis client utilities.

Provides async + sync Redis clients for pub/sub and general use.
PR-D7 phase 3b: was wired to Celery's broker; now reads `settings.REDIS_URL`
which still falls back to the legacy `CELERY_BROKER_URL` env var.

Async clients are kept per-event-loop via a ``WeakKeyDictionary``. ``aioredis``
connection pools bind to the loop that first awaits them; sharing one
``aioredis.Redis`` across multiple loops (e.g. uvicorn + an ``asyncio.run()``
sub-loop in a DBOS workflow) causes "got Future attached to a different loop"
crashes. Same root cause family as the Supabase singleton fix — see
``app/db/supabase_client.py`` for the full rationale.
"""

import asyncio
import weakref
from typing import MutableMapping

import redis as sync_redis_pkg
import redis.asyncio as aioredis

from app.core.config import settings

_async_redis_clients: MutableMapping[asyncio.AbstractEventLoop, aioredis.Redis] = (
    weakref.WeakKeyDictionary()
)
_sync_redis: sync_redis_pkg.Redis | None = None


async def get_async_redis() -> aioredis.Redis:
    """Return the current loop's async Redis client (lazily created)."""
    loop = asyncio.get_running_loop()
    client = _async_redis_clients.get(loop)
    if client is not None:
        return client
    client = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )
    _async_redis_clients[loop] = client
    return client


async def close_async_redis() -> None:
    """Close the current loop's async Redis client (call on app shutdown).

    Only closes the client bound to the loop running this coroutine. Other
    loops' clients are owned by those loops and will be GC'd with them — we
    never call ``aclose()`` on a client bound to a different loop.
    """
    loop = asyncio.get_running_loop()
    client = _async_redis_clients.pop(loop, None)
    if client is not None:
        await client.aclose()


def get_sync_redis() -> sync_redis_pkg.Redis:
    """Return a shared sync Redis client built from `settings.REDIS_URL`."""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = sync_redis_pkg.Redis.from_url(settings.REDIS_URL)
    return _sync_redis
