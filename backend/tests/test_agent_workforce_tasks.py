"""Unit tests for the M2 workforce Celery beat tasks.

Mocks the Supabase RPC layer so we never hit a real DB. Verifies:
    - Top-level advisory lock is taken AND released around each tick
    - Lock contention path returns {'skipped': 1, ...}
    - Lock release happens even when the inner tick raises
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks import agent_workforce_tasks as wf


# ─── shared fakes ────────────────────────────────────────────────────


def _patch_lock_client(*, lock_acquired: bool):
    """Patch get_async_supabase_admin to return a client whose
    try_advisory_lock returns the given bool, and tracks unlock calls."""
    rpc_calls: list[tuple[str, dict]] = []

    def _make_rpc(name: str, payload: dict):
        rpc_calls.append((name, payload))
        chain = MagicMock()
        if name == "try_advisory_lock":
            chain.execute = AsyncMock(
                return_value=MagicMock(data=lock_acquired)
            )
        else:  # advisory_unlock
            chain.execute = AsyncMock(return_value=MagicMock(data=True))
        return chain

    fake_client = MagicMock()
    fake_client.rpc = _make_rpc

    async def _client_factory():
        return fake_client

    return _client_factory, rpc_calls


# ─── inbox tick ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_tick_skipped_when_lock_held():
    factory, rpc_calls = _patch_lock_client(lock_acquired=False)
    with patch.object(wf, "get_async_supabase_admin", factory):
        result = await wf._process_inbox_async()
    assert result["skipped"] == 1
    # Lock release should NOT be called when we never acquired it.
    assert not any(name == "advisory_unlock" for name, _ in rpc_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_tick_runs_processor_when_lock_acquired():
    factory, rpc_calls = _patch_lock_client(lock_acquired=True)
    fake_processor = MagicMock()
    fake_processor.tick = AsyncMock(
        return_value={"agents_processed": 2, "tasks_created": 1, "errors": 0}
    )
    with (
        patch.object(wf, "get_async_supabase_admin", factory),
        patch.object(wf, "InboxProcessor", return_value=fake_processor),
    ):
        result = await wf._process_inbox_async()
    assert result["skipped"] == 0
    assert result["tasks_created"] == 1
    fake_processor.tick.assert_awaited_once()
    # Lock must be released
    assert any(name == "advisory_unlock" for name, _ in rpc_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_tick_releases_lock_on_processor_exception():
    """Crashing processor must not leak the advisory lock."""
    factory, rpc_calls = _patch_lock_client(lock_acquired=True)
    fake_processor = MagicMock()
    fake_processor.tick = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch.object(wf, "get_async_supabase_admin", factory),
        patch.object(wf, "InboxProcessor", return_value=fake_processor),
    ):
        with pytest.raises(RuntimeError):
            await wf._process_inbox_async()

    assert any(name == "advisory_unlock" for name, _ in rpc_calls)


# ─── outbox tick ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_tick_skipped_when_lock_held():
    factory, rpc_calls = _patch_lock_client(lock_acquired=False)
    with patch.object(wf, "get_async_supabase_admin", factory):
        result = await wf._dispatch_outbox_async()
    assert result["skipped"] == 1
    assert not any(name == "advisory_unlock" for name, _ in rpc_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_tick_runs_dispatcher_when_lock_acquired():
    factory, rpc_calls = _patch_lock_client(lock_acquired=True)
    fake_disp = MagicMock()
    fake_disp.tick = AsyncMock(
        return_value={
            "delivered_user": 3,
            "delivered_agent": 1,
            "errors": 0,
        }
    )
    with (
        patch.object(wf, "get_async_supabase_admin", factory),
        patch.object(wf, "OutboxDispatcher", return_value=fake_disp),
    ):
        result = await wf._dispatch_outbox_async()
    assert result["skipped"] == 0
    assert result["delivered_user"] == 3
    fake_disp.tick.assert_awaited_once()
    assert any(name == "advisory_unlock" for name, _ in rpc_calls)


# ─── distinct lock keys ──────────────────────────────────────────────


@pytest.mark.unit
def test_lock_keys_are_distinct():
    """Inbox / outbox / sweeper must hold different lock keys, otherwise
    they'd serialise needlessly."""
    from app.tasks.agent_runs_sweeper import SWEEPER_LOCK_KEY

    keys = {
        "inbox": wf.INBOX_LOCK_KEY,
        "outbox": wf.OUTBOX_LOCK_KEY,
        "sweeper": SWEEPER_LOCK_KEY,
    }
    assert len(set(keys.values())) == 3, f"lock keys collide: {keys}"
