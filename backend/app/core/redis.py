# app/core/redis.py

"""
Redis client utilities.

Provides async + sync Redis clients for pub/sub and general use.
PR-D7 phase 3: dropped the celery_app.backend.client indirection;
both helpers now construct redis-py clients directly from the
broker URL setting.
"""

import redis as sync_redis_pkg
import redis.asyncio as aioredis

from app.core.config import settings

_async_redis: aioredis.Redis | None = None
_sync_redis: sync_redis_pkg.Redis | None = None


async def get_async_redis() -> aioredis.Redis:
    """Return a shared async Redis connection for pub/sub and general use."""
    global _async_redis
    if _async_redis is None:
        _async_redis = aioredis.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
        )
    return _async_redis


async def close_async_redis():
    """Close the shared async Redis connection (call on app shutdown)."""
    global _async_redis
    if _async_redis is not None:
        await _async_redis.aclose()
        _async_redis = None


def get_sync_redis() -> sync_redis_pkg.Redis:
    """Return a shared sync Redis client.

    PR-D7: was `celery_app.backend.client` — now a plain redis-py
    client built from `settings.CELERY_BROKER_URL` (the env var name
    is preserved for backward compatibility; the URL points at the
    broker Redis instance regardless of whether Celery is in use)."""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = sync_redis_pkg.Redis.from_url(settings.CELERY_BROKER_URL)
    return _sync_redis
