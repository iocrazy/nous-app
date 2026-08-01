"""Reply-driven resume for needs_input issues (spec §4).

仅当 needs_followup + agent_outcome=needs_input 时回复才驱动状态流转;
其它状态回复保持 Spec-1b 的"不改状态"。"""
from unittest.mock import AsyncMock

import pytest

from app.workflows.issue_lifecycle import _run_reply_turns, route_finish_outcome


@pytest.mark.asyncio
async def test_route_completed_autoclose_off_goes_in_review():
    set_status = AsyncMock()
    await route_finish_outcome(1, "completed", "r", auto_close=False, set_status=set_status)
    set_status.assert_awaited_once_with(
        1, "in_review", agent_outcome="completed", outcome_reason="r"
    )


@pytest.mark.asyncio
async def test_route_needs_input_goes_needs_followup():
    set_status = AsyncMock()
    await route_finish_outcome(1, "needs_input", "which style?", auto_close=True, set_status=set_status)
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
