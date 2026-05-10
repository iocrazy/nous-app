"""asyncpg pool to Supavisor — direct PG, no HTTP layer.

Migration from supabase-py / httpx / PostgREST / Kong to asyncpg +
Supavisor (transaction pooler). Treats Bug C (httpx stale-pool
ReadError after ~8h backend uptime) at the architectural level
rather than patching the httpx layer.

Lifecycle:
  - Pool is lazily created on first ``get_pool()`` call
  - FastAPI ``shutdown`` event drains it via ``close_pool()``
  - Concurrent first-request races are guarded by an asyncio Lock
  - When ``SUPAVISOR_DATABASE_URL`` is empty the module is a no-op
    and ``get_pool()`` raises so callers can fall back to the
    legacy supabase-py path

Why ``statement_cache_size=0``:
  Supavisor in transaction mode multiplexes one PG backend across
  many client transactions. Prepared statements live on the backend,
  not the connection — caching their plans on the asyncpg side
  causes "prepared statement does not exist" errors when the next
  txn lands on a different backend. Disabling client-side cache
  is the documented Supavisor + asyncpg pattern.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import asyncpg
from loguru import logger

from app.core.config import settings

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


def is_configured() -> bool:
    """True when SUPAVISOR_DATABASE_URL is set. Repository code uses
    this to decide whether to take the asyncpg fast path or fall
    back to the supabase-py legacy path during the migration window."""
    return bool(settings.SUPAVISOR_DATABASE_URL)


async def get_pool() -> asyncpg.Pool:
    """Return the singleton asyncpg pool. Lazily created on first call.

    Raises ``RuntimeError`` if the pool isn't configured (caller is
    expected to fall back to supabase-py during the migration window).
    """
    if not is_configured():
        raise RuntimeError(
            "SUPAVISOR_DATABASE_URL is not configured — pg_pool is "
            "disabled. Either set the env var or use supabase-py."
        )

    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:  # double-check after lock
            return _pool

        logger.info(
            "[pg_pool] creating asyncpg pool min={} max={}",
            settings.SUPAVISOR_POOL_MIN_SIZE,
            settings.SUPAVISOR_POOL_MAX_SIZE,
        )
        _pool = await asyncpg.create_pool(
            dsn=settings.SUPAVISOR_DATABASE_URL,
            min_size=settings.SUPAVISOR_POOL_MIN_SIZE,
            max_size=settings.SUPAVISOR_POOL_MAX_SIZE,
            statement_cache_size=0,  # required for transaction-mode pooler
            command_timeout=60.0,
            server_settings={"application_name": "mediahub_backend"},
        )
        logger.info("[pg_pool] pool ready")
        return _pool


async def close_pool() -> None:
    """Drain the pool. FastAPI shutdown event hook calls this so we
    don't leak idle connections at restart."""
    global _pool
    if _pool is None:
        return
    logger.info("[pg_pool] closing pool")
    try:
        await _pool.close()
    except Exception:
        # Best-effort: shutdown failures shouldn't crash the process
        logger.exception("[pg_pool] close raised — proceeding")
    finally:
        _pool = None


async def health_check() -> bool:
    """Quick liveness probe. Used by /health endpoint and the FastAPI
    startup hook to fail fast on misconfiguration. Returns False
    rather than raising so monitoring code stays simple."""
    if not is_configured():
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
            return val == 1
    except Exception:
        logger.exception("[pg_pool] health_check failed")
        return False


__all__ = [
    "close_pool",
    "get_pool",
    "health_check",
    "is_configured",
]
