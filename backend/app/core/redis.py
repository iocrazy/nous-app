# app/core/redis.py

"""
Redis client utilities.

Provides async Redis client for WebSocket pub/sub and a sync convenience accessor.
"""

import redis.asyncio as aioredis

from app.core.config import settings

_async_redis: aioredis.Redis | None = None


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


def get_sync_redis():
    """Return the sync Redis client used by Celery backend.

    Must be called after Celery app is initialized (i.e. inside tasks or API handlers).
    """
    from app.celery_app import celery_app

    return celery_app.backend.client
