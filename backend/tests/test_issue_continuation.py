"""Spec-2: issue dispatch consumes the agent's FinishIssue declaration to drive
status + bounded continuation.

Tests the pure orchestration core ``_run_dispatch_with_continuation`` with
injected fakes (mirrors the _run_reply_turns testability idiom) — no DBOS / DB.
"""

from __future__ import annotations

import pytest

from app.workflows.issue_lifecycle import _run_dispatch_with_continuation


class _Recorder:
    """Captures set_status calls + scripts run_turn outcomes."""

    def __init__(self, outcomes: list[tuple[str | None, str | None]]):
        self._outcomes = list(outcomes)
        self.turns: list[bool] = []  # is_continuation per call
        self.status_calls: list[dict] = []

    async def run_turn(self, issue_row, agent_id, user_id, *, is_continuation):
        self.turns.append(is_continuation)
        outcome, reason = self._outcomes[len(self.turns) - 1]
        return {"content": "x", "outcome": outcome, "reason": reason}

    async def set_status(
        self,
        issue_id,
        status,
        *,
        error_code=None,
        error_message=None,
        agent_outcome=None,
        outcome_reason=None,
    ):
        self.status_calls.append(
            {
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "agent_outcome": agent_outcome,
                "outcome_reason": outcome_reason,
            }
        )


def _const_load_issue(status: str = "in_progress"):
    """A load_issue fake that always reports the same issue status."""

    async def _load(issue_id):
        return {"id": issue_id, "status": status}

    return _load


def _scripted_load_issue(statuses: list[str]):
    """A load_issue fake that walks a script of statuses, one per call, then
    repeats the last entry (mirrors _Recorder's outcome scripting)."""
    seq = list(statuses)

    async def _load(issue_id):
        idx = min(_load.calls, len(seq) - 1)
        _load.calls += 1
        return {"id": issue_id, "status": seq[idx]}

    _load.calls = 0
    return _load


async def _run(rec: _Recorder, max_continuations=2, auto_close=False, load_issue=None):
    return await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent-1",
        "user-1",
        run_turn=rec.run_turn,
        set_status=rec.set_status,
        load_issue=load_issue or _const_load_issue(),
        max_continuations=max_continuations,
        auto_close=auto_close,
    )


@pytest.mark.asyncio
async def test_completed_goes_in_review_with_outcome():
    rec = _Recorder([("completed", "all done")])
    await _run(rec)
    assert rec.turns == [False]  # single turn, not a continuation
    assert rec.status_calls == [
        {
            "status": "in_review",
            "error_code": None,
            "error_message": None,
            "agent_outcome": "completed",
            "outcome_reason": "all done",
        }
    ]


@pytest.mark.asyncio
async def test_completed_auto_closes_to_done_when_enabled():
    # slice 2a: with the platform toggle on, completed → done (self-close).
    rec = _Recorder([("completed", "all done")])
    await _run(rec, auto_close=True)
    call = rec.status_calls[-1]
    assert call["status"] == "done"
    assert call["agent_outcome"] == "completed"


@pytest.mark.asyncio
async def test_completed_stays_in_review_when_auto_close_off():
    # Default: even a completed declaration parks at in_review for a human.
    rec = _Recorder([("completed", "all done")])
    await _run(rec, auto_close=False)
    assert rec.status_calls[-1]["status"] == "in_review"


@pytest.mark.asyncio
async def test_auto_close_only_applies_to_completed_not_continue_cap():
    # continue-capped must NOT auto-close even when the toggle is on — the agent
    # never said it finished.
    rec = _Recorder([("continue", "a"), ("continue", "b"), ("continue", "c")])
    await _run(rec, max_continuations=2, auto_close=True)
    assert rec.status_calls[-1]["status"] == "in_review"
    assert rec.status_calls[-1]["agent_outcome"] == "continue_capped"


@pytest.mark.asyncio
async def test_needs_input_goes_needs_followup():
    # slice 2b: needs_input → needs_followup (deliberate hand-off), NOT blocked.
    rec = _Recorder([("needs_input", "need the API key")])
    await _run(rec)
    call = rec.status_calls[-1]
    assert call["status"] == "needs_followup"
    assert call["agent_outcome"] == "needs_input"
    assert call["outcome_reason"] == "need the API key"


@pytest.mark.asyncio
async def test_continue_loops_until_completed_before_cap():
    rec = _Recorder([("continue", "wip"), ("completed", "done")])
    await _run(rec)
    assert rec.turns == [False, True]  # second turn is a continuation
    assert rec.status_calls[-1]["status"] == "in_review"
    assert rec.status_calls[-1]["agent_outcome"] == "completed"


@pytest.mark.asyncio
async def test_continue_hits_cap_then_in_review_capped():
    # Always asks to continue → bounded at max_continuations, then handed to human.
    rec = _Recorder([("continue", "a"), ("continue", "b"), ("continue", "c")])
    await _run(rec, max_continuations=2)
    # 1 initial + 2 continuations = 3 turns, then stop
    assert rec.turns == [False, True, True]
    assert rec.status_calls[-1]["status"] == "in_review"
    assert rec.status_calls[-1]["agent_outcome"] == "continue_capped"


@pytest.mark.asyncio
async def test_no_declaration_falls_back_to_in_review_plain():
    rec = _Recorder([(None, None)])
    await _run(rec)
    call = rec.status_calls[-1]
    assert call["status"] == "in_review"
    assert call["agent_outcome"] is None
    assert call["error_code"] is None


# --- External-state preemption (Symphony §16.5 per-turn reconciliation) -------


@pytest.mark.asyncio
async def test_preempt_before_first_turn_when_already_cancelled():
    # Issue cancelled between dispatch and the first turn → never run, never
    # overwrite the external status.
    rec = _Recorder([("completed", "should not run")])
    res = await _run(rec, load_issue=_const_load_issue("cancelled"))
    assert rec.turns == []  # no agent turn burned
    assert rec.status_calls == []  # external status left untouched
    assert res["preempted"] is True
    assert res["preempted_status"] == "cancelled"
    assert res["attempts"] == 0


@pytest.mark.asyncio
async def test_preempt_between_continuation_turns():
    # First check active → run turn (continue); before the 2nd turn the issue is
    # cancelled externally → preempt instead of burning the rest of the budget.
    rec = _Recorder([("continue", "wip"), ("completed", "should not run")])
    res = await _run(
        rec,
        max_continuations=2,
        load_issue=_scripted_load_issue(["in_progress", "cancelled"]),
    )
    assert rec.turns == [False]  # only the first turn ran
    assert rec.status_calls == []  # don't clobber the external cancel
    assert res["preempted"] is True
    assert res["preempted_status"] == "cancelled"
    assert res["attempts"] == 1


@pytest.mark.asyncio
async def test_preempt_on_external_done():
    # Any terminal status (not just cancelled) preempts.
    rec = _Recorder([("continue", "wip"), ("continue", "wip2")])
    res = await _run(
        rec,
        load_issue=_scripted_load_issue(["in_progress", "done"]),
    )
    assert rec.turns == [False]
    assert res["preempted"] is True
    assert res["preempted_status"] == "done"


@pytest.mark.asyncio
async def test_no_preempt_when_status_stays_in_progress():
    # The agent's own completion (status stays in_progress through the loop, set
    # only after) must NOT be mistaken for an external stop.
    rec = _Recorder([("completed", "done")])
    res = await _run(rec, load_issue=_const_load_issue("in_progress"))
    assert rec.turns == [False]
    assert res.get("preempted") is not True
    assert rec.status_calls[-1]["status"] == "in_review"
