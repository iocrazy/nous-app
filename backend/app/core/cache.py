"""Lightweight in-memory TTL cache for hot backend queries.

Hot paths (user_settings, frontend_config, points pricing) are currently
reading from Supabase on every request. A short-lived process-local cache
absorbs most of the hits with minimal staleness cost.

Not distributed — each uvicorn worker holds its own copy. For sub-second
TTLs across workers, reach for Redis instead.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class _CacheEntry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    """Process-local async TTL cache. Thread-safe via asyncio.Lock."""

    def __init__(self, ttl_seconds: float, maxsize: int = 1024) -> None:
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._store: dict[str, _CacheEntry[T]] = {}
        self._lock = asyncio.Lock()

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        """Return cached value or load via `loader` and cache the result."""
        now = time.monotonic()
        entry = self._store.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value

        async with self._lock:
            # Double-check under lock to avoid thundering herd
            entry = self._store.get(key)
            now = time.monotonic()
            if entry is not None and entry.expires_at > now:
                return entry.value

            value = await loader()
            self._store[key] = _CacheEntry(value=value, expires_at=now + self._ttl)

            # Simple size cap: drop oldest when exceeded
            if len(self._store) > self._maxsize:
                oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
                self._store.pop(oldest_key, None)

            return value

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# Shared caches. Keep TTLs short: correctness beats cache hit rate here.
user_settings_cache: TTLCache[Optional[dict[str, Any]]] = TTLCache(ttl_seconds=30.0)
frontend_config_cache: TTLCache[dict[str, Any]] = TTLCache(ttl_seconds=60.0)
points_pricing_cache: TTLCache[Optional[dict[str, Any]]] = TTLCache(ttl_seconds=300.0)
