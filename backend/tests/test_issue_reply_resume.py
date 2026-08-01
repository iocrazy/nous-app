"""Reply-driven resume for needs_input issues (spec §4).

仅当 needs_followup + agent_outcome=needs_input 时回复才驱动状态流转;
其它状态回复保持 Spec-1b 的"不改状态"。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.issue_lifecycle import (
    _pending_agent_outcome,
    _run_reply_turns,
    route_finish_outcome,
)


def test_pending_agent_outcome_reads_valid_json_string():
    issue = {"execution_state": '{"agent_outcome": "needs_input"}'}
    assert _pending_agent_outcome(issue) == "needs_input"


def test_pending_agent_outcome_none_on_malformed_json_string():
    issue = {"execution_state": "{not valid json"}
    assert _pending_agent_outcome(issue) is None


def test_pending_agent_outcome_reads_native_dict():
    # Defensive path: some callers may hand a jsonb column already decoded to
    # a dict rather than the raw string load_issue's engine returns.
    issue = {"execution_state": {"agent_outcome": "needs_input"}}
    assert _pending_agent_outcome(issue) == "needs_input"


def test_pending_agent_outcome_none_when_missing_or_none():
    assert _pending_agent_outcome({}) is None
    assert _pending_agent_outcome({"execution_state": None}) is None


@pytest.mark.asyncio
async def test_route_completed_autoclose_off_goes_in_review():
    set_status = AsyncMock()
    await route_finish_outcome(
        1, "completed", "r", auto_close=False, set_status=set_status
    )
    set_status.assert_awaited_once_with(
        1, "in_review", agent_outcome="completed", outcome_reason="r"
    )


@pytest.mark.asyncio
async def test_route_needs_input_goes_needs_followup():
    set_status = AsyncMock()
    await route_finish_outcome(
        1, "needs_input", "which style?", auto_close=True, set_status=set_status
    )
    set_status.assert_awaited_once_with(
        1, "needs_followup", agent_outcome="needs_input", outcome_reason="which style?"
    )


@pytest.mark.asyncio
async def test_reply_to_needs_input_issue_resumes_and_reroutes():
    """needs_followup+needs_input 的 issue 收到回复:先转 in_progress,
    续 turn 后按新 outcome 路由(这里 agent 答 completed)。"""
    calls = []

    async def fake_set_status(issue_id, status, **kw):
        calls.append(status)

    async def fake_load_issue(issue_id):
        return {
            "id": issue_id,
            "status": "needs_followup",
            "execution_state": '{"agent_outcome": "needs_input"}',
        }

    async def fake_run_turn(**kw):
        return {"content": "done", "outcome": "completed", "reason": None}

    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    sleep = AsyncMock()

    out = await _run_reply_turns(
        7,
        "u",
        "the style is X",
        session_id="s",
        acquire=acquire,
        run_turn=fake_run_turn,
        release=release,
        sleep=sleep,
        load_issue=fake_load_issue,
        set_status=fake_set_status,
        auto_close=False,
    )

    assert calls == ["in_progress", "in_review"]
    assert out["executed"] is True
    release.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_reply_to_normal_issue_keeps_status_untouched():
    """in_progress 的 issue 收到回复:set_status 从未被调用(Spec-1b 回归)。"""
    set_status = AsyncMock()

    async def fake_load_issue(issue_id):
        return {"id": issue_id, "status": "in_progress", "execution_state": None}

    async def fake_run_turn(**kw):
        return {"content": "ok", "outcome": None, "reason": None}

    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    sleep = AsyncMock()

    out = await _run_reply_turns(
        7,
        "u",
        "just a note",
        session_id="s",
        acquire=acquire,
        run_turn=fake_run_turn,
        release=release,
        sleep=sleep,
        load_issue=fake_load_issue,
        set_status=set_status,
        auto_close=False,
    )

    set_status.assert_not_awaited()
    assert out["executed"] is True


@pytest.mark.asyncio
async def test_reply_resume_turn_raises_leaves_issue_blocked_not_stuck():
    """Parked finding (Task 1 review, same silent-failure theme as A2): if
    ``set_status(in_progress)`` succeeds and the resumed turn then raises,
    the issue must not be left stuck in_progress forever — mirror
    execute_issue's own outer try/except (blocked + typed error, re-raise)."""
    calls = []

    async def fake_set_status(issue_id, status, **kw):
        calls.append((status, kw.get("error_code")))

    async def fake_load_issue(issue_id):
        return {
            "id": issue_id,
            "status": "needs_followup",
            "execution_state": '{"agent_outcome": "needs_input"}',
        }

    async def fake_run_turn(**kw):
        raise RuntimeError("llm blew up")

    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    sleep = AsyncMock()

    with pytest.raises(RuntimeError, match="llm blew up"):
        await _run_reply_turns(
            7,
            "u",
            "the style is X",
            session_id="s",
            acquire=acquire,
            run_turn=fake_run_turn,
            release=release,
            sleep=sleep,
            load_issue=fake_load_issue,
            set_status=fake_set_status,
            auto_close=False,
        )

    assert calls == [("in_progress", None), ("blocked", "issue_reply_resume_failed")]
    release.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_multi_round_needs_input_cycle_completes_on_second_answer():
    """I2 (final review): the full cycle the spec names — needs_input→回答→
    in_progress→再 needs_input→再回答→completed — not just one round. The
    fake ``set_status`` mimics the REAL set_status's execution_state
    overwrite semantics (full-replace only when agent_outcome/outcome_reason
    is passed; untouched otherwise) so round 2's ``load_issue`` sees exactly
    what round 1 actually wrote, same as production."""
    issue_state = {
        "status": "needs_followup",
        "execution_state": {"agent_outcome": "needs_input"},
    }
    calls: list[str] = []

    async def fake_set_status(
        issue_id, status, *, agent_outcome=None, outcome_reason=None, **kw
    ):
        calls.append(status)
        issue_state["status"] = status
        state: dict = {}
        if agent_outcome:
            state["agent_outcome"] = agent_outcome
        if outcome_reason:
            state["outcome_reason"] = outcome_reason
        if state:
            issue_state["execution_state"] = state

    async def fake_load_issue(issue_id):
        return {
            "id": issue_id,
            "status": issue_state["status"],
            "execution_state": json.dumps(issue_state["execution_state"]),
        }

    turn_outcomes = iter(
        [
            {
                "content": "which style?",
                "outcome": "needs_input",
                "reason": "which style?",
            },
            {"content": "done", "outcome": "completed", "reason": None},
        ]
    )

    async def fake_run_turn(**kw):
        return next(turn_outcomes)

    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    sleep = AsyncMock()

    # Round 1: needs_input → answer → in_progress → re needs_input.
    out1 = await _run_reply_turns(
        7,
        "u",
        "first answer",
        session_id="s",
        acquire=acquire,
        run_turn=fake_run_turn,
        release=release,
        sleep=sleep,
        load_issue=fake_load_issue,
        set_status=fake_set_status,
        auto_close=False,
    )
    assert out1["executed"] is True
    assert calls == ["in_progress", "needs_followup"]
    assert issue_state["status"] == "needs_followup"
    assert issue_state["execution_state"]["agent_outcome"] == "needs_input"

    # Round 2: re needs_input → re answer → in_progress → completed.
    out2 = await _run_reply_turns(
        7,
        "u",
        "second answer",
        session_id="s",
        acquire=acquire,
        run_turn=fake_run_turn,
        release=release,
        sleep=sleep,
        load_issue=fake_load_issue,
        set_status=fake_set_status,
        auto_close=False,
    )
    assert out2["executed"] is True
    assert calls == ["in_progress", "needs_followup", "in_progress", "in_review"]
    assert issue_state["status"] == "in_review"
    assert issue_state["execution_state"]["agent_outcome"] == "completed"
    assert release.await_count == 2


@pytest.mark.asyncio
async def test_resume_reply_that_completes_fires_subissue_barrier():
    """I4 (final review): a child issue resumed via a reply-answer that
    routes to a terminal outcome (here `done`, auto_close=True) must fire the
    sub-issue barrier hook — mirrors execute_issue's own fan-in call after
    routing. Without this, a child completed through the reply-resume path
    (rather than the dispatch loop) never wakes its parent's barrier, and a
    parent waiting on that sibling hangs forever."""

    async def fake_set_status(issue_id, status, **kw):
        pass

    async def fake_load_issue(issue_id):
        return {
            "id": issue_id,
            "status": "needs_followup",
            "execution_state": '{"agent_outcome": "needs_input"}',
        }

    async def fake_run_turn(**kw):
        return {"content": "done", "outcome": "completed", "reason": None}

    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    sleep = AsyncMock()
    barrier = AsyncMock()

    with patch("app.workflows.issue_lifecycle._maybe_fire_subissue_barrier", barrier):
        await _run_reply_turns(
            9,
            "u",
            "the answer",
            session_id="s",
            acquire=acquire,
            run_turn=fake_run_turn,
            release=release,
            sleep=sleep,
            load_issue=fake_load_issue,
            set_status=fake_set_status,
            auto_close=True,
        )

    barrier.assert_awaited_once_with(9)


@pytest.mark.asyncio
async def test_non_resume_reply_never_fires_subissue_barrier():
    """A plain comment on an issue that isn't parked at needs_input never
    touches status (Spec-1b) — there is nothing for the barrier to react to,
    so it must not fire."""
    set_status = AsyncMock()

    async def fake_load_issue(issue_id):
        return {"id": issue_id, "status": "in_progress", "execution_state": None}

    async def fake_run_turn(**kw):
        return {"content": "ok", "outcome": None, "reason": None}

    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    sleep = AsyncMock()
    barrier = AsyncMock()

    with patch("app.workflows.issue_lifecycle._maybe_fire_subissue_barrier", barrier):
        await _run_reply_turns(
            9,
            "u",
            "just a note",
            session_id="s",
            acquire=acquire,
            run_turn=fake_run_turn,
            release=release,
            sleep=sleep,
            load_issue=fake_load_issue,
            set_status=set_status,
            auto_close=False,
        )

    barrier.assert_not_awaited()
