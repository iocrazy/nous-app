"""fh4 T2 (E2b/E2e): the minute sweeper carries out the reaper's wake orders in
the workflow BODY, and says so when a sub-agent result sits unclaimed.

The reaper runs inside ``reap_stale_workforce_tasks_step``. DBOS refuses
``start_workflow`` from inside a step, so the step returns the order and the
body dispatches it (same split as ``_drain_one_issue``).
"""

from __future__ import annotations

import datetime as dt
import inspect
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.workflows import agent_runs_sweeper as sw

pytestmark = pytest.mark.unit

_STEPS = (
    "mark_heartbeat_lost_step",
    "recompute_monthly_budgets_step",
    "expire_orphan_inbox_step",
    "reconcile_issue_execution_state_step",
    "force_settle_stale_pending_trees_step",
    "reap_preempted_input_waits_step",
    "reap_zombie_locks_step",
)


def _body(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _quiet_tick(monkeypatch, stale):
    for name in _STEPS:
        monkeypatch.setattr(sw, name, AsyncMock(return_value=0))
    monkeypatch.setattr(sw, "scan_idle_inbox_step", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        sw, "reap_stale_workforce_tasks_step", AsyncMock(return_value=stale)
    )


async def test_the_body_dispatches_the_reapers_wake_orders(monkeypatch):
    from app.workflows import agent_workforce

    order = {"task_id": "t1", "idle_dispatch": {"issue_id": 7, "user_id": "u"}}
    _quiet_tick(monkeypatch, {"candidates": 1, "failed": 1, "wake_orders": [order]})
    wake = AsyncMock()
    monkeypatch.setattr(agent_workforce, "_dispatch_idle_wake", wake)

    await _body(sw.agent_runs_sweeper_workflow)(None, None)

    wake.assert_awaited_once_with(order)


async def test_a_checkpoint_from_before_fh4_has_no_wake_orders(monkeypatch):
    """A tick recovered across the deploy replays the OLD step output."""
    from app.workflows import agent_workforce

    _quiet_tick(monkeypatch, {"candidates": 1, "failed": 1})
    wake = AsyncMock()
    monkeypatch.setattr(agent_workforce, "_dispatch_idle_wake", wake)

    await _body(sw.agent_runs_sweeper_workflow)(None, None)

    wake.assert_not_awaited()


def test_the_reaper_step_itself_never_dispatches():
    src = inspect.getsource(_body(sw.reap_stale_workforce_tasks_step))
    assert "deliver_or_dispatch" not in src and "_dispatch_idle_wake" not in src


# ── unclaimed-result alert ───────────────────────────────────────────────────


def test_alert_stmt_marks_once_and_only_stale_unclaimed_subagent_results():
    from app.repositories.agent_run_inbox_repository import (
        unclaimed_results_alert_stmt,
    )

    now = dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc)
    sql = str(
        unclaimed_results_alert_stmt(
            older_than=now - dt.timedelta(minutes=30), now=now
        ).compile(dialect=postgresql.dialect())
    )
    assert sql.startswith("UPDATE public.agent_run_inbox")
    assert "public.agent_run_inbox.kind = " in sql
    assert "claimed_at IS NULL" in sql and "expired_at IS NULL" in sql
    assert "public.agent_run_inbox.created_at < " in sql
    assert "NOT (public.agent_run_inbox.content ? " in sql
    assert "RETURNING" in sql


async def test_alert_logs_one_error_per_item(monkeypatch):
    import app.repositories.agent_run_inbox_repository as repo_mod

    rows = [
        {"id": 1, "target_kind": "issue", "target_id": 7, "created_at": None},
        {"id": 2, "target_kind": "conversation", "target_id": 9, "created_at": None},
    ]
    mark = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        repo_mod,
        "get_agent_run_inbox_repository",
        lambda: type("R", (), {"mark_unclaimed_results_alerted": mark})(),
    )
    errors: list[str] = []
    monkeypatch.setattr(sw.logger, "error", lambda m: errors.append(m))

    n = await sw.alert_unclaimed_subagent_results()

    assert n == 2 and len(errors) == 2
    older_than = mark.await_args.kwargs["older_than"]
    age = dt.datetime.now(dt.timezone.utc) - older_than
    assert abs(age.total_seconds() - 30 * 60) < 5


async def test_the_alert_runs_inside_the_orphan_expiry_step(monkeypatch):
    import app.repositories.agent_run_inbox_repository as repo_mod

    alert = AsyncMock(return_value=0)
    monkeypatch.setattr(sw, "alert_unclaimed_subagent_results", alert)
    expire = AsyncMock(return_value=3)
    monkeypatch.setattr(
        repo_mod,
        "get_agent_run_inbox_repository",
        lambda: type("R", (), {"expire_stale": expire})(),
    )

    assert await _body(sw.expire_orphan_inbox_step)() == 3
    alert.assert_awaited_once()


async def test_a_failing_alert_does_not_block_the_expiry(monkeypatch):
    import app.repositories.agent_run_inbox_repository as repo_mod

    monkeypatch.setattr(
        sw,
        "alert_unclaimed_subagent_results",
        AsyncMock(side_effect=RuntimeError("db blip")),
    )
    expire = AsyncMock(return_value=1)
    monkeypatch.setattr(
        repo_mod,
        "get_agent_run_inbox_repository",
        lambda: type("R", (), {"expire_stale": expire})(),
    )

    assert await _body(sw.expire_orphan_inbox_step)() == 1
