"""Phase 2a Task 5: a paused turn leaves the issue at ``in_progress`` with
``paused_at`` as the only truth — no status routing, no FinishIssue outcome."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from app.workflows import issue_lifecycle as il

pytestmark = pytest.mark.unit


def _load_issue(status="in_progress", paused_at=None):
    async def load(issue_id):
        return {"id": issue_id, "status": status, "paused_at": paused_at}

    return load


def _gate():
    async def wait_for_input(issue_id, *, ttl_seconds):
        return {"reply_text": "go on", "user_id": "u1"}

    async def mark_waiting(issue_id, prompt, *, question=None):
        pass

    async def clear_waiting(issue_id):
        pass

    return wait_for_input, mark_waiting, clear_waiting


async def test_paused_stop_reason_returns_without_routing_status(monkeypatch):
    route = AsyncMock()
    monkeypatch.setattr(il, "route_finish_outcome", route)
    set_status = AsyncMock()

    async def run_turn(*a, **k):
        return {"content": "", "stop_reason": "paused", "run_id": "31"}

    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(),
    )
    assert res["outcome"] == "paused" and res["paused"] is True
    assert res["run_id"] == "31"
    set_status.assert_not_awaited()  # status stays in_progress
    route.assert_not_awaited()  # no FinishIssue routing on a pause


async def test_issue_already_paused_at_loop_top_does_not_run_a_turn(monkeypatch):
    run_turn = AsyncMock()
    set_status = AsyncMock()
    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(paused_at="2026-09-08T00:00:00+00:00"),
    )
    assert res["outcome"] == "paused" and res["paused"] is True
    run_turn.assert_not_awaited()
    set_status.assert_not_awaited()


async def test_paused_reply_turn_after_a_wait_also_returns_paused(monkeypatch):
    """The reply turn that follows a needs_input wake can be paused too."""
    monkeypatch.setattr(il, "_backfill_run_issue_id", AsyncMock())
    monkeypatch.setattr(il, "_question_for_park", AsyncMock(return_value=None))
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append(status)

    async def run_turn(*a, **k):
        return {"content": "", "outcome": "needs_input", "reason": "which?"}

    async def run_reply(issue_id, payload):
        return {"content": "", "stop_reason": "paused"}

    wait_for_input, mark_waiting, clear_waiting = _gate()
    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(),
        wait_for_input=wait_for_input,
        mark_waiting=mark_waiting,
        clear_waiting=clear_waiting,
        run_reply=run_reply,
    )
    assert res["outcome"] == "paused" and res["wait_rounds"] == 1
    # the park + the wake happened; nothing after the paused reply
    assert statuses == ["needs_followup", "in_progress"]


async def test_cancelled_stop_reason_still_routes_as_before(monkeypatch):
    """Only ``paused`` short-circuits; a cancel keeps today's routing."""
    route = AsyncMock()
    monkeypatch.setattr(il, "route_finish_outcome", route)

    async def run_turn(*a, **k):
        return {"content": "", "stop_reason": "cancelled", "cancelled": True}

    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=_load_issue(),
    )
    assert res["outcome"] is None and "paused" not in res
    route.assert_awaited_once()


def test_execute_issue_releases_the_lock_in_finally():
    src = inspect.getsource(il.execute_issue)
    assert (
        "finally:" in src and "await clear_lock(issue_id)" in src.split("finally:")[1]
    )
