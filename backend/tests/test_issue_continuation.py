"""Spec-2: issue dispatch consumes the agent's FinishIssue declaration to drive
status + bounded continuation.

Tests the pure orchestration core ``_run_dispatch_with_continuation`` with
injected fakes (mirrors the _run_reply_turns testability idiom) — no DBOS / DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

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


# ---------- needs_input 等待循环（spec 2026-07-30） ----------


def _mk_wait_deps(payloads):
    """payloads: 依次弹出的回复 payload（None=超时）。返回 (deps kwargs, calls 记录)。"""
    calls = {"mark": 0, "clear": 0, "replies": []}
    seq = list(payloads)

    async def wait_for_input(issue_id, ttl_seconds):
        return seq.pop(0) if seq else None

    async def mark_waiting(issue_id, prompt):
        calls["mark"] += 1

    async def clear_waiting(issue_id):
        calls["clear"] += 1

    async def run_reply(issue_id, payload):
        calls["replies"].append(payload["reply_text"])
        return {"outcome": "completed", "reason": "done after reply"}

    return (
        dict(
            wait_for_input=wait_for_input,
            mark_waiting=mark_waiting,
            clear_waiting=clear_waiting,
            run_reply=run_reply,
        ),
        calls,
    )


@pytest.mark.asyncio
async def test_needs_input_waits_then_reply_continues_to_completed():
    """needs_input → 挂起 → 收到回复 → 回复回合 completed → in_review。"""
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append((status, kw.get("agent_outcome")))

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "?", "outcome": "needs_input", "reason": "which ending?"}

    deps, calls = _mk_wait_deps(
        [{"reply_text": "摊牌", "user_id": "u1", "attachments": None}]
    )
    result = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        **deps,
    )
    assert result["outcome"] == "completed"
    assert result["wait_rounds"] == 1
    assert calls == {"mark": 1, "clear": 1, "replies": ["摊牌"]}
    # 状态序列：等待时 needs_followup → 回复后 in_progress → 终态 in_review
    assert statuses[0] == ("needs_followup", "needs_input")
    assert statuses[1][0] == "in_progress"
    assert statuses[-1][0] == "in_review"


@pytest.mark.asyncio
async def test_needs_input_timeout_terminates_like_today():
    """超时（wait 返 None）→ 清标记 → 停在 needs_followup，与现状终态一致。"""
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append(status)

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "?", "outcome": "needs_input", "reason": "which ending?"}

    deps, calls = _mk_wait_deps([None])
    result = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        **deps,
    )
    assert result["outcome"] == "needs_input"
    assert calls["clear"] == 1
    assert statuses[-1] == "needs_followup"  # 没有回到 in_progress


@pytest.mark.asyncio
async def test_needs_input_wait_rounds_capped():
    """agent 连环问人 → 第 NEEDS_INPUT_MAX_WAIT_ROUNDS 轮后强制终结。"""

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "?", "outcome": "needs_input", "reason": "again?"}

    async def run_reply(issue_id, payload):
        return {"outcome": "needs_input", "reason": "and again?"}

    deps, calls = _mk_wait_deps(
        [
            {"reply_text": f"r{i}", "user_id": "u", "attachments": None}
            for i in range(10)
        ]
    )
    deps["run_reply"] = run_reply
    result = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        **deps,
    )
    assert result["wait_rounds"] == 5  # settings 默认
    assert result["outcome"] == "needs_input"  # 超限按现状终结


@pytest.mark.asyncio
async def test_needs_input_without_gate_behaves_as_today():
    """不注入 gate（生产未接线/故障降级）→ 现状行为：直接 needs_followup 终结。"""
    statuses = []

    async def set_status(issue_id, status, **kw):
        statuses.append(status)

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "?", "outcome": "needs_input", "reason": "?"}

    result = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=set_status,
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
    )
    assert result["outcome"] == "needs_input"
    assert statuses == ["needs_followup"]


@pytest.mark.asyncio
async def test_wait_wakeup_preempted_by_external_close():
    """挂起期间 issue 被人工关闭 → 唤醒后 PREEMPT 复查让路，不再跑回复回合。"""
    load_seq = [{"status": "in_progress"}, {"status": "cancelled"}]

    async def load_issue(_):
        return load_seq.pop(0) if load_seq else {"status": "cancelled"}

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "?", "outcome": "needs_input", "reason": "?"}

    deps, calls = _mk_wait_deps(
        [{"reply_text": "r", "user_id": "u", "attachments": None}]
    )
    result = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=load_issue,
        **deps,
    )
    assert result.get("preempted") is True
    assert calls["replies"] == []  # 回复回合没有跑
