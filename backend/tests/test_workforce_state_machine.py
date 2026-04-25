"""Unit tests for WorkerStateMachine.

The state machine is the workforce's gatekeeper — illegal transitions
must blow up loudly, the advisory lock must always release, and the
audit row must always land. These tests pin those invariants.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.services.workforce.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    LockNotAcquiredError,
    WorkerStateMachine,
    agent_lock_key,
)


# ─── helpers ──────────────────────────────────────────────────────────


def _build_repo(*, current_state: str | None) -> MagicMock:
    """A repo whose get_worker returns a worker dict at `current_state`,
    or None when current_state is None (first-touch)."""
    repo = MagicMock()
    if current_state is None:
        repo.get_worker = AsyncMock(return_value=None)
    else:
        repo.get_worker = AsyncMock(return_value={"state": current_state})
    repo.upsert_worker = AsyncMock(return_value={"agent_id": str(uuid4())})
    repo.update_worker_state = AsyncMock(return_value=True)
    repo.log_state_transition = AsyncMock(return_value=True)
    return repo


def _patch_lock(sm: WorkerStateMachine, *, acquired: bool, unlock_calls: list) -> None:
    """Replace the supabase-rpc-based lock with a deterministic shim."""
    rpc_chain = MagicMock()
    rpc_chain.execute = AsyncMock(
        return_value=MagicMock(data=acquired)
    )

    unlock_chain = MagicMock()
    unlock_chain.execute = AsyncMock(return_value=MagicMock(data=True))

    fake_client = MagicMock()

    def _rpc(name, payload):
        if name == "try_advisory_lock":
            return rpc_chain
        unlock_calls.append((name, payload))
        return unlock_chain

    fake_client.rpc = _rpc

    async def _client_factory():
        return fake_client

    # Monkey-patch the get_async_supabase_admin used inside _hold_lock.
    import app.services.workforce.state_machine as sm_mod

    sm_mod.get_async_supabase_admin = _client_factory  # type: ignore[assignment]


# ─── lock key determinism ────────────────────────────────────────────


@pytest.mark.unit
def test_agent_lock_key_is_deterministic_and_in_int8_range():
    aid = UUID("00000000-0000-4000-8000-000000000001")
    key1 = agent_lock_key(aid)
    key2 = agent_lock_key(aid)
    assert key1 == key2
    # Positive int8: 0 <= key < 2^63
    assert 0 <= key1 < 2**63


@pytest.mark.unit
def test_agent_lock_key_distinct_for_distinct_uuids():
    a, b = uuid4(), uuid4()
    assert agent_lock_key(a) != agent_lock_key(b)


# ─── transition table sanity ─────────────────────────────────────────


@pytest.mark.unit
def test_transition_table_targets_are_valid_states():
    valid = {
        "idle", "working", "waiting_for_other",
        "blocked", "paused", "terminated",
    }
    for (_, _trigger), to_state in ALLOWED_TRANSITIONS.items():
        assert to_state in valid, f"Bad target: {to_state}"


# ─── happy-path transition ───────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_idle_to_working_persists_and_logs():
    repo = _build_repo(current_state="idle")
    sm = WorkerStateMachine(repo=repo)
    unlocks: list = []
    _patch_lock(sm, acquired=True, unlock_calls=unlocks)

    agent_id = uuid4()
    task_id = uuid4()
    result = await sm.transition(
        agent_id=agent_id,
        trigger="task_assigned",
        task_id=task_id,
        current_task_id=task_id,
    )
    assert result.from_state == "idle"
    assert result.to_state == "working"

    repo.update_worker_state.assert_awaited_once()
    repo.log_state_transition.assert_awaited_once()
    # Lock must have been released exactly once.
    assert any(name == "advisory_unlock" for name, _ in unlocks)


# ─── illegal transitions raise ───────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_invalid_pair_raises_and_does_not_persist():
    repo = _build_repo(current_state="idle")
    sm = WorkerStateMachine(repo=repo)
    unlocks: list = []
    _patch_lock(sm, acquired=True, unlock_calls=unlocks)

    with pytest.raises(InvalidTransitionError) as excinfo:
        await sm.transition(
            agent_id=uuid4(), trigger="task_completed"
        )
    # Caller can introspect the move
    assert excinfo.value.from_state == "idle"
    assert excinfo.value.trigger == "task_completed"

    # Nothing persisted on illegal move
    repo.update_worker_state.assert_not_called()
    repo.log_state_transition.assert_not_called()
    # Lock still released (finally clause)
    assert any(name == "advisory_unlock" for name, _ in unlocks)


# ─── wildcard triggers fire from any state ───────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("from_state", ["idle", "working", "waiting_for_other"])
async def test_error_trigger_routes_to_blocked_from_any_state(from_state: str):
    repo = _build_repo(current_state=from_state)
    sm = WorkerStateMachine(repo=repo)
    unlocks: list = []
    _patch_lock(sm, acquired=True, unlock_calls=unlocks)

    result = await sm.transition(agent_id=uuid4(), trigger="error")
    assert result.to_state == "blocked"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_pause_and_resume_round_trip():
    repo = _build_repo(current_state="working")
    sm = WorkerStateMachine(repo=repo)
    unlocks: list = []
    _patch_lock(sm, acquired=True, unlock_calls=unlocks)
    agent_id = uuid4()

    pause_res = await sm.transition(agent_id=agent_id, trigger="admin_pause")
    assert pause_res.to_state == "paused"

    # Switch the repo to report 'paused' so admin_resume can fire.
    repo.get_worker = AsyncMock(return_value={"state": "paused"})
    resume_res = await sm.transition(agent_id=agent_id, trigger="admin_resume")
    assert resume_res.to_state == "idle"


# ─── lock contention ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_raises_when_lock_unavailable():
    repo = _build_repo(current_state="idle")
    sm = WorkerStateMachine(repo=repo)
    unlocks: list = []
    _patch_lock(sm, acquired=False, unlock_calls=unlocks)

    with pytest.raises(LockNotAcquiredError):
        await sm.transition(agent_id=uuid4(), trigger="task_assigned")
    # No state changes when we couldn't lock
    repo.update_worker_state.assert_not_called()
    repo.log_state_transition.assert_not_called()
    # And no unlock attempt either (we never had the lock)
    assert all(name != "advisory_unlock" for name, _ in unlocks)


# ─── first-touch (no row yet) ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_first_touch_registers_then_moves():
    """If the worker row doesn't exist yet, the SM should upsert it as idle
    then re-resolve the trigger."""
    repo = _build_repo(current_state=None)
    sm = WorkerStateMachine(repo=repo)
    unlocks: list = []
    _patch_lock(sm, acquired=True, unlock_calls=unlocks)

    result = await sm.transition(agent_id=uuid4(), trigger="task_assigned")
    repo.upsert_worker.assert_awaited()
    assert result.from_state == "idle"
    assert result.to_state == "working"


# ─── force_terminate skips the lock ──────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_force_terminate_does_not_take_lock():
    """Sweeper path: lock-free force termination still records history."""
    repo = _build_repo(current_state="working")
    sm = WorkerStateMachine(repo=repo)

    # Patch lock to fail — proving force_terminate doesn't reach it.
    unlocks: list = []
    _patch_lock(sm, acquired=False, unlock_calls=unlocks)

    result = await sm.force_terminate(agent_id=uuid4(), reason="heartbeat_lost")
    assert result.to_state == "terminated"
    repo.update_worker_state.assert_awaited()
    repo.log_state_transition.assert_awaited()
    # No unlock RPC was even attempted
    assert all(name != "advisory_unlock" for name, _ in unlocks)
