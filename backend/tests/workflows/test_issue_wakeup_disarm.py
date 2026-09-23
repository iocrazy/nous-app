"""Defect B (2026-09-23 prod S2): an agent's wake-up chain outlived the work.

The per-run cap of ``ScheduleWakeup`` gives every woken run a fresh budget,
so once the issue went ``in_review`` each fire started a new billed run that
armed more wake-ups. Two cuts, both scoped to rows the AGENT armed:

* ``route_finish_outcome`` disarms them when the agent declares ``completed``
  or is capped on ``continue`` (pause does NOT disarm — ruling 2);
* ``_fire_issue_wakeup`` refuses an agent row whose issue is ``in_review`` /
  ``needs_followup`` and disables it with ``issue_not_active``.

A user's wake-up keeps the behaviour spec §5 promised: it stops only when
the issue is done/cancelled."""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest

from app.workflows import issue_lifecycle as il
from app.workflows import scheduled_master as sm

pytestmark = pytest.mark.unit


def _row(created_by: Optional[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"issue_id": 7, "text": "ping", "once": True}
    if created_by is not None:
        payload["created_by"] = created_by
    return {"id": "s1", "next_fire_at": None, "user_id": "u1", "payload": payload}


@pytest.fixture
def disable(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(sm, "_disable_schedule", mock)
    return mock


def _status(status: str):
    return AsyncMock(return_value={"status": status, "hidden_at": None})


# ── the firing guard ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_review", "needs_followup"])
async def test_agent_wakeup_on_an_inactive_issue_is_disabled_not_fired(
    monkeypatch, disable, status
):
    monkeypatch.setattr(sm, "_load_issue", _status(status))
    assert await sm._fire_issue_wakeup(_row("agent")) is None
    disable.assert_awaited_once_with("s1", "issue_not_active", bump_skipped=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("created_by", ["user", None])
@pytest.mark.parametrize("status", ["in_review", "needs_followup"])
async def test_user_wakeup_on_an_inactive_issue_still_fires(
    monkeypatch, disable, status, created_by
):
    monkeypatch.setattr(sm, "_load_issue", _status(status))
    order = await sm._fire_issue_wakeup(_row(created_by))
    assert order is not None and order["kind"] == "issue_wakeup"
    disable.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "todo"])
async def test_agent_wakeup_on_a_live_issue_is_still_delivered(
    monkeypatch, disable, status
):
    # in_progress: the order goes to deliver_or_dispatch, which puts it in the
    # inbox while a run or a pause holds the issue (unchanged behaviour).
    monkeypatch.setattr(sm, "_load_issue", _status(status))
    order = await sm._fire_issue_wakeup(_row("agent"))
    assert order is not None and order["source"]["created_by"] == "agent"
    disable.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("created_by", ["agent", "user"])
@pytest.mark.parametrize("status", ["done", "cancelled"])
async def test_terminal_issue_keeps_the_existing_guard(
    monkeypatch, disable, status, created_by
):
    monkeypatch.setattr(sm, "_load_issue", _status(status))
    assert await sm._fire_issue_wakeup(_row(created_by)) is None
    disable.assert_awaited_once_with("s1", "issue_terminal", bump_skipped=True)


@pytest.mark.asyncio
async def test_an_inactive_skip_never_reads_as_having_fired(monkeypatch, disable):
    monkeypatch.setattr(sm, "_load_issue", _status("in_review"))
    row = {**_row("agent"), "task_type": "issue_wakeup", "cron_expr": None}
    assert await sm._dispatch_one(row) == {"outcome": "skipped"}


def test_issue_not_active_is_a_proven_terminal_reason():
    # Should the reason ever travel through finish_issue_wakeup_step, it must
    # disable the row, not book a failure and retry every minute.
    assert not sm._is_delivery_failure("skipped", "issue_not_active")


# ── route_finish_outcome ────────────────────────────────────────────────────


async def _route(outcome, *, content_len=5, auto_close=False):
    set_status = AsyncMock(return_value=True)
    disarm = AsyncMock(return_value=2)
    await il.route_finish_outcome(
        7,
        outcome,
        "r",
        auto_close=auto_close,
        set_status=set_status,
        content_len=content_len,
        disarm_wakeups=disarm,
    )
    return set_status, disarm


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_close", [False, True])
async def test_completed_disarms_agent_wakeups(auto_close):
    _, disarm = await _route("completed", auto_close=auto_close)
    disarm.assert_awaited_once_with(7, "issue_not_active")


@pytest.mark.asyncio
async def test_continue_capped_disarms_agent_wakeups():
    _, disarm = await _route("continue")
    disarm.assert_awaited_once_with(7, "issue_not_active")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, content_len",
    [("needs_input", 5), (None, 5), (None, 0)],
    ids=["needs_input", "no_declaration", "empty_output"],
)
async def test_other_outcomes_leave_wakeups_armed(outcome, content_len):
    _, disarm = await _route(outcome, content_len=content_len)
    disarm.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_default_disarm_is_the_module_helper(monkeypatch):
    step = AsyncMock(return_value=0)
    monkeypatch.setattr(il, "_disarm_agent_wakeups", step)
    await il.route_finish_outcome(
        7, "completed", None, auto_close=False, set_status=AsyncMock(), content_len=3
    )
    step.assert_awaited_once_with(7, "issue_not_active")


@pytest.mark.asyncio
async def test_the_helper_logs_a_failure_instead_of_failing_the_workflow(monkeypatch):
    from app.repositories import user_schedules_repository as repo

    monkeypatch.setattr(
        repo, "disarm_agent_wakeups", AsyncMock(side_effect=RuntimeError("db down"))
    )
    errors: list[str] = []
    monkeypatch.setattr(il.logger, "error", lambda msg, *a, **k: errors.append(msg))
    assert await il._disarm_agent_wakeups(7, "issue_not_active") is None
    assert errors and "issue 7" in errors[0]


def test_the_disarm_helper_is_not_a_dbos_step():
    """Not a step, on purpose. Its UPDATE filters on ``enabled`` so a replay
    is a no-op and needs no checkpoint; and a new step inside the workflow
    body shifts every later step's recorded sequence number, so a workflow
    recovered across the deploy would hit a step-name mismatch."""
    import inspect

    fn = il._disarm_agent_wakeups
    assert not hasattr(fn, "dbos_function_name")
    assert inspect.unwrap(fn) is fn
