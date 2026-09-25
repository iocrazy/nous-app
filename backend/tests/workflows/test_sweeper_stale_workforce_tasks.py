"""framework-hardening T5: the minute sweeper reaps stale workforce tasks near
the END of the tick (a step added mid-sequence shifts the step ids of in-flight
scheduled workflows across a deploy). FH3 T6 appended one step after it."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock

import pytest

from app.workflows import agent_runs_sweeper as sw

pytestmark = pytest.mark.unit


def _body(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


async def test_step_delegates_to_the_reaper(monkeypatch):
    from app.services.workforce import stale_tasks

    reap = AsyncMock(return_value={"failed": 1, "requeued": 2})
    monkeypatch.setattr(stale_tasks, "reap_stale_workforce_tasks", reap)
    out = await _body(sw.reap_stale_workforce_tasks_step)()
    assert out == {"failed": 1, "requeued": 2}
    reap.assert_awaited_once_with()


def _awaited_step_names(fn) -> list[str]:
    tree = ast.parse(inspect.getsource(_body(fn)))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id.endswith("_step"):
                names.append((node.lineno, func.id))
    return [n for _, n in sorted(names)]


def test_reap_steps_keep_their_order_at_the_end_of_the_tick():
    """Steps are only ever appended: FH3 T6's zombie-lock reaper is now last,
    the workforce reaper second to last, the preempted-wait reaper third."""
    steps = _awaited_step_names(sw.agent_runs_sweeper_workflow)
    assert steps[-1] == "reap_zombie_locks_step", steps
    assert steps[-2] == "reap_stale_workforce_tasks_step", steps
    assert steps[-3] == "reap_preempted_input_waits_step", steps


async def test_tick_logs_the_stale_task_counts(monkeypatch):
    for name in (
        "mark_heartbeat_lost_step",
        "recompute_monthly_budgets_step",
        "expire_orphan_inbox_step",
        "reconcile_issue_execution_state_step",
        "force_settle_stale_pending_trees_step",
        "reap_preempted_input_waits_step",
        "reap_zombie_locks_step",
    ):
        monkeypatch.setattr(sw, name, AsyncMock(return_value=0))
    monkeypatch.setattr(sw, "scan_idle_inbox_step", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        sw,
        "reap_stale_workforce_tasks_step",
        AsyncMock(return_value={"candidates": 1, "failed": 1, "requeued": 0}),
    )
    seen = []
    monkeypatch.setattr(sw.logger, "info", lambda m: seen.append(m))
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert any("stale_workforce_tasks=" in m and "failed=1" in m for m in seen), seen


async def test_skips_alone_keep_the_tick_quiet(monkeypatch):
    for name in (
        "mark_heartbeat_lost_step",
        "recompute_monthly_budgets_step",
        "expire_orphan_inbox_step",
        "reconcile_issue_execution_state_step",
        "force_settle_stale_pending_trees_step",
        "reap_preempted_input_waits_step",
        "reap_zombie_locks_step",
    ):
        monkeypatch.setattr(sw, name, AsyncMock(return_value=0))
    monkeypatch.setattr(sw, "scan_idle_inbox_step", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        sw,
        "reap_stale_workforce_tasks_step",
        AsyncMock(
            return_value={"candidates": 2, "skipped_pending": 1, "skipped_running": 1}
        ),
    )
    seen = []
    monkeypatch.setattr(sw.logger, "info", lambda m: seen.append(m))
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert seen == []
