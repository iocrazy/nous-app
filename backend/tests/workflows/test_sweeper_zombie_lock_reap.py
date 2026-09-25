"""FH3 T6: the minute sweeper releases execution locks whose workflow is
already terminal (or gone), as its new LAST step."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from app.workflows import agent_runs_sweeper as sw

pytestmark = pytest.mark.unit

_OTHER_STEPS = (
    "mark_heartbeat_lost_step",
    "recompute_monthly_budgets_step",
    "expire_orphan_inbox_step",
    "reconcile_issue_execution_state_step",
    "force_settle_stale_pending_trees_step",
    "reap_preempted_input_waits_step",
)


def _body(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _quiet(monkeypatch, zombie_locks):
    for name in _OTHER_STEPS:
        monkeypatch.setattr(sw, name, AsyncMock(return_value=0))
    monkeypatch.setattr(sw, "scan_idle_inbox_step", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        sw, "reap_stale_workforce_tasks_step", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        sw, "reap_zombie_locks_step", AsyncMock(return_value=zombie_locks)
    )
    seen: list[str] = []
    monkeypatch.setattr(sw.logger, "info", lambda m: seen.append(m))
    return seen


async def test_step_delegates_to_the_input_gate_reaper(monkeypatch):
    from app.agent_framework import input_gate

    reap = AsyncMock(return_value=5)
    monkeypatch.setattr(input_gate, "reap_zombie_locks", reap)
    assert await _body(sw.reap_zombie_locks_step)() == 5
    reap.assert_awaited_once_with()


def test_tick_calls_the_step():
    assert "reap_zombie_locks_step()" in inspect.getsource(
        sw.agent_runs_sweeper_workflow
    )


async def test_tick_logs_the_released_count(monkeypatch):
    seen = _quiet(monkeypatch, 5)
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert any("zombie_locks_released=5" in m for m in seen), seen


async def test_zero_released_keeps_the_tick_quiet(monkeypatch):
    seen = _quiet(monkeypatch, 0)
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert seen == []


async def test_other_activity_does_not_print_a_zero_count(monkeypatch):
    seen = _quiet(monkeypatch, 0)
    monkeypatch.setattr(sw, "expire_orphan_inbox_step", AsyncMock(return_value=2))
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert seen and not any("zombie_locks_released" in m for m in seen), seen
