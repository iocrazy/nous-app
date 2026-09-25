"""Hotfix-2 defect H: the worker's minute sweeper finishes a parked workflow
whose issue was already preempted (cancelled/done/closed) but whose release
could not run where the cancel landed (the API process)."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from app.workflows import agent_runs_sweeper as sw

pytestmark = pytest.mark.unit


def _body(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


async def test_step_delegates_to_the_input_gate_reaper(monkeypatch):
    from app.agent_framework import input_gate

    reap = AsyncMock(return_value=2)
    monkeypatch.setattr(input_gate, "reap_preempted_input_waits", reap)
    assert await _body(sw.reap_preempted_input_waits_step)() == 2
    reap.assert_awaited_once_with()


def test_tick_calls_the_reap_step():
    assert "reap_preempted_input_waits_step()" in inspect.getsource(
        sw.agent_runs_sweeper_workflow
    )


async def test_tick_logs_the_reaped_count(monkeypatch):
    for name in (
        "mark_heartbeat_lost_step",
        "recompute_monthly_budgets_step",
        "expire_orphan_inbox_step",
        "reconcile_issue_execution_state_step",
        "force_settle_stale_pending_trees_step",
    ):
        monkeypatch.setattr(sw, name, AsyncMock(return_value=0))
    monkeypatch.setattr(sw, "scan_idle_inbox_step", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        sw, "reap_preempted_input_waits_step", AsyncMock(return_value=1)
    )
    monkeypatch.setattr(
        sw, "reap_stale_workforce_tasks_step", AsyncMock(return_value={})
    )
    monkeypatch.setattr(sw, "reap_zombie_locks_step", AsyncMock(return_value=0))
    seen = []
    monkeypatch.setattr(sw.logger, "info", lambda m: seen.append(m))
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert any("preempted_waits_released=1" in m for m in seen), seen
