"""Regression: the 'already completed' dedup guard must be independent of the
Redis lock TTL.

Bug: a download that completed hours ago has no Redis dedup lock left (TTL=1h),
so on re-dispatch `SET NX` succeeded and returned 'created' — re-downloading
already-completed work — because the completed-check only ran when the lock was
still held. The fix moves the completed-check (keyed on the stable
dedup_identifier, e.g. platform_id) ahead of the SET NX.
"""

from unittest.mock import MagicMock

import pytest

from app.services.infra.unified_task_manager import UnifiedTaskManager


@pytest.mark.asyncio
async def test_skips_when_completed_even_though_lock_expired(monkeypatch):
    mgr = UnifiedTaskManager()
    # Redis lock is GONE (expired): SET NX would succeed if reached.
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    monkeypatch.setattr(mgr, "_get_redis", lambda: fake_redis)

    async def _completed(_key):
        return True

    monkeypatch.setattr(mgr, "_completed_task_exists", _completed)

    result = await mgr.acquire_or_subscribe(
        task_type="download:video",
        dedup_identifier="plat123",
        user_id="user1",
        resource_id="res1",
    )

    assert result["action"] == "completed"
    # Proves the completed-check runs BEFORE the lock acquire — SET NX never tried.
    fake_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_creates_when_not_completed_and_lock_free(monkeypatch):
    mgr = UnifiedTaskManager()
    fake_redis = MagicMock()
    fake_redis.set.return_value = True  # NX acquires
    monkeypatch.setattr(mgr, "_get_redis", lambda: fake_redis)

    async def _not_completed(_key):
        return False

    monkeypatch.setattr(mgr, "_completed_task_exists", _not_completed)

    result = await mgr.acquire_or_subscribe(
        task_type="download:video",
        dedup_identifier="plat123",
        user_id="user1",
        resource_id="res1",
    )

    assert result["action"] == "created"
    assert result["dedup_key"] == "task:download:video:plat123"


@pytest.mark.asyncio
async def test_completed_probe_failure_is_non_fatal(monkeypatch):
    """A DB hiccup on the completed-probe must not block dispatch (fail-open)."""
    mgr = UnifiedTaskManager()
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    monkeypatch.setattr(mgr, "_get_redis", lambda: fake_redis)

    async def _boom(_key):
        raise RuntimeError("db down")

    monkeypatch.setattr(mgr, "_completed_task_exists", _boom)

    result = await mgr.acquire_or_subscribe(
        task_type="download:video",
        dedup_identifier="plat123",
        user_id="user1",
        resource_id="res1",
    )
    assert result["action"] == "created"
