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


async def _run(rec: _Recorder, max_continuations=2):
    return await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent-1",
        "user-1",
        run_turn=rec.run_turn,
        set_status=rec.set_status,
        max_continuations=max_continuations,
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
async def test_needs_input_goes_blocked():
    rec = _Recorder([("needs_input", "need the API key")])
    await _run(rec)
    call = rec.status_calls[-1]
    assert call["status"] == "blocked"
    assert call["error_code"] == "agent_needs_input"
    assert call["error_message"] == "need the API key"
    assert call["agent_outcome"] == "needs_input"


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
