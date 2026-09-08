"""Phase 2a Task 5: a paused turn leaves the issue at ``in_progress`` with
``paused_at`` as the only truth — no status routing, no FinishIssue outcome."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from app.workflows import issue_lifecycle as il

pytestmark = pytest.mark.unit


def _load_issue(status="in_progress", paused_at=None, *, paused_from_call=None):
    """``paused_from_call``: the 1-based load_issue call from which the issue
    reads as paused (a pause landing mid-dispatch)."""
    calls = {"n": 0}

    async def load(issue_id):
        calls["n"] += 1
        p = paused_at
        if paused_from_call is not None and calls["n"] >= paused_from_call:
            p = "2026-09-08T00:00:00+00:00"
        return {"id": issue_id, "status": status, "paused_at": p}

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


async def test_pause_landing_after_a_needs_input_turn_still_parks_the_question(
    monkeypatch,
):
    """The pause check must not sit above the needs_input park: a pause that
    lands between the turn and the loop top would otherwise drop the park
    (no needs_followup, no marker — the agent's question lost)."""
    monkeypatch.setattr(il, "_backfill_run_issue_id", AsyncMock())
    monkeypatch.setattr(il, "_question_for_park", AsyncMock(return_value=None))
    marks, statuses = [], []

    async def set_status(issue_id, status, **kw):
        statuses.append(status)

    async def mark_waiting(issue_id, prompt, *, question=None):
        marks.append(prompt)

    async def wait_for_input(issue_id, *, ttl_seconds):
        return None  # timeout while paused: stays parked

    async def clear_waiting(issue_id):
        pass

    async def run_turn(*a, **k):
        return {"content": "", "outcome": "needs_input", "reason": "which?"}

    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=_load_issue(paused_from_call=2),
        wait_for_input=wait_for_input,
        mark_waiting=mark_waiting,
        clear_waiting=clear_waiting,
        run_reply=AsyncMock(),
    )
    assert marks == ["which?"] and "needs_followup" in statuses
    assert res["outcome"] == "needs_input"


async def test_answer_wakes_one_reply_turn_then_the_pause_stops_continuation(
    monkeypatch,
):
    """An answer is a wake (spec §2): the reply turn runs even while paused;
    its ``continue`` is where the pause takes effect."""
    monkeypatch.setattr(il, "_backfill_run_issue_id", AsyncMock())
    monkeypatch.setattr(il, "_question_for_park", AsyncMock(return_value=None))
    route = AsyncMock()
    monkeypatch.setattr(il, "route_finish_outcome", route)
    wait_for_input, mark_waiting, clear_waiting = _gate()
    run_reply = AsyncMock(return_value={"content": "more", "outcome": "continue"})

    async def run_turn(*a, **k):
        return {"content": "", "outcome": "needs_input", "reason": "which?"}

    res = await il._run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=_load_issue(paused_from_call=2),
        wait_for_input=wait_for_input,
        mark_waiting=mark_waiting,
        clear_waiting=clear_waiting,
        run_reply=run_reply,
    )
    run_reply.assert_awaited_once()
    assert res["outcome"] == "paused" and res["wait_rounds"] == 1
