"""Unit tests for WorkerStateMachine.

The state machine is the workforce's gatekeeper — illegal transitions
must blow up loudly and the audit row must always land. These tests
pin those invariants.

The advisory lock that earlier revisions used was removed in the
TODO-AI-012 cleanup (see state_machine.py module docstring), so these
tests no longer assert anything about lock acquisition / release.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.services.workforce.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    WorkerStateMachine,
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


# ─── illegal transitions raise ───────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_invalid_pair_raises_and_does_not_persist():
    repo = _build_repo(current_state="idle")
    sm = WorkerStateMachine(repo=repo)

    with pytest.raises(InvalidTransitionError) as excinfo:
        await sm.transition(
            agent_id=uuid4(), trigger="task_completed"
        )
    assert excinfo.value.from_state == "idle"
    assert excinfo.value.trigger == "task_completed"

    # Nothing persisted on illegal move
    repo.update_worker_state.assert_not_called()
    repo.log_state_transition.assert_not_called()


# ─── wildcard triggers fire from any state ───────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("from_state", ["idle", "working", "waiting_for_other"])
async def test_error_trigger_routes_to_blocked_from_any_state(from_state: str):
    repo = _build_repo(current_state=from_state)
    sm = WorkerStateMachine(repo=repo)

    result = await sm.transition(agent_id=uuid4(), trigger="error")
    assert result.to_state == "blocked"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_pause_and_resume_round_trip():
    repo = _build_repo(current_state="working")
    sm = WorkerStateMachine(repo=repo)
    agent_id = uuid4()

    pause_res = await sm.transition(agent_id=agent_id, trigger="admin_pause")
    assert pause_res.to_state == "paused"

    # Switch the repo to report 'paused' so admin_resume can fire.
    repo.get_worker = AsyncMock(return_value={"state": "paused"})
    resume_res = await sm.transition(agent_id=agent_id, trigger="admin_resume")
    assert resume_res.to_state == "idle"


# ─── first-touch (no row yet) ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_first_touch_registers_then_moves():
    """If the worker row doesn't exist yet, the SM should upsert it as idle
    then re-resolve the trigger."""
    repo = _build_repo(current_state=None)
    sm = WorkerStateMachine(repo=repo)

    result = await sm.transition(agent_id=uuid4(), trigger="task_assigned")
    repo.upsert_worker.assert_awaited()
    assert result.from_state == "idle"
    assert result.to_state == "working"


# ─── force_terminate ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_force_terminate_records_history():
    """Sweeper path: force termination always lands a state row + history."""
    repo = _build_repo(current_state="working")
    sm = WorkerStateMachine(repo=repo)

    result = await sm.force_terminate(agent_id=uuid4(), reason="heartbeat_lost")
    assert result.to_state == "terminated"
    repo.update_worker_state.assert_awaited()
    repo.log_state_transition.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_force_terminate_first_touch_creates_then_logs():
    repo = _build_repo(current_state=None)
    sm = WorkerStateMachine(repo=repo)

    result = await sm.force_terminate(agent_id=uuid4(), reason="heartbeat_lost")
    assert result.to_state == "terminated"
    assert result.from_state is None
    repo.upsert_worker.assert_awaited()
    repo.log_state_transition.assert_awaited()


# ─── UUID parameter sanity ───────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_passes_correct_args_to_repo():
    """Pin the repo invocation contract — agent_id, state, transition fields."""
    repo = _build_repo(current_state="idle")
    sm = WorkerStateMachine(repo=repo)
    agent_id = UUID("00000000-0000-4000-8000-000000000001")
    task_id = uuid4()

    await sm.transition(
        agent_id=agent_id,
        trigger="task_assigned",
        task_id=task_id,
        current_task_id=task_id,
    )

    update_call = repo.update_worker_state.await_args
    assert update_call.kwargs["agent_id"] == agent_id
    assert update_call.kwargs["state"] == "working"

    log_call = repo.log_state_transition.await_args
    assert log_call.kwargs["from_state"] == "idle"
    assert log_call.kwargs["to_state"] == "working"
    assert log_call.kwargs["trigger"] == "task_assigned"
    assert log_call.kwargs["task_id"] == task_id
