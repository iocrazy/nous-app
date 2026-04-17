"""Unit tests for the in-memory TTL cache (app/core/cache.py)."""

from __future__ import annotations

import asyncio

import pytest

from app.core.cache import TTLCache


@pytest.mark.asyncio
async def test_cache_hit_skips_loader() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return 42

    first = await cache.get_or_load("k", loader)
    second = await cache.get_or_load("k", loader)

    assert first == 42
    assert second == 42
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_miss_calls_loader() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)

    async def loader() -> int:
        return 7

    assert await cache.get_or_load("a", loader) == 7
    assert await cache.get_or_load("b", loader) == 7


@pytest.mark.asyncio
async def test_invalidate_forces_reload() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    counter = [0]

    async def loader() -> int:
        counter[0] += 1
        return counter[0]

    assert await cache.get_or_load("k", loader) == 1
    cache.invalidate("k")
    assert await cache.get_or_load("k", loader) == 2


@pytest.mark.asyncio
async def test_thundering_herd_guard() -> None:
    """Concurrent misses should call the loader at most once under the lock."""
    cache: TTLCache[int] = TTLCache(ttl_seconds=60)
    call_count = 0

    async def slow_loader() -> int:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return 99

    results = await asyncio.gather(
        *[cache.get_or_load("k", slow_loader) for _ in range(10)]
    )

    assert all(r == 99 for r in results)
    assert call_count == 1


@pytest.mark.asyncio
async def test_zero_ttl_always_refreshes() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=0)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    r1 = await cache.get_or_load("k", loader)
    r2 = await cache.get_or_load("k", loader)

    # With ttl=0, expires_at equals "now", so the next read must reload
    assert r1 == 1
    assert r2 == 2
