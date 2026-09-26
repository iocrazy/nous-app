"""run_issue_reply_step: same hook, same result key — but only on a resuming
turn, and without a retry (the reply road has no continuation turn)."""

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests

USER = "22222222-2222-2222-2222-222222222222"


def _wire(monkeypatch, *, status, outcome="completed"):
    from app.workflows import issue_lifecycle as m

    calls = (
        []
        if outcome is None
        else [
            {
                "name": "FinishIssue",
                "args": {"outcome": outcome},
                "result": {"ok": True, "outcome": outcome},
            }
        ]
    )
    chat = AsyncMock()
    chat.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {
                "id": "m1",
                "role": "assistant",
                "content": "ok",
                "metadata_json": {},
            },
            "run_id": "r9",
            "tool_calls": calls,
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    read = AsyncMock(return_value=status)
    monkeypatch.setattr(m, "read_issue_status", read)
    hook = AsyncMock(
        return_value=("completed", None, {"verdict": "pass", "attempt": 1})
    )
    monkeypatch.setattr(m, "apply_completion_verification", hook)
    return m, read, hook


async def _step(m):
    return await m.run_issue_reply_step.__wrapped__(
        issue_id=7, session_id="123", user_id=USER, reply_text="hi"
    )


async def test_reply_step_verifies_a_completed_declaration_on_a_resuming_turn(
    monkeypatch,
):
    """_run_reply_turns flips a resuming issue to in_progress before the turn;
    that status is the step's only view of 'this reply will be routed'."""
    m, read, hook = _wire(monkeypatch, status="in_progress")
    out = await _step(m)
    assert out["verification"] == {"verdict": "pass", "attempt": 1}
    kw = hook.await_args.kwargs
    assert (
        kw["trigger"] == "issue_reply"
        and kw["issue_id"] == 7
        and kw["result"]["run_id"] == "r9"
    )
    # the reply road has no retry turn: a rejection must stay completed
    assert kw["allow_retry"] is False
    read.assert_awaited_once_with(7, purpose="reply_verification")


@pytest.mark.parametrize("status", ["in_review", "done", "needs_followup", None])
async def test_reply_step_does_not_verify_a_non_resuming_turn(monkeypatch, status):
    """A plain comment leaves the status where it was — nothing will route
    this declaration, so no judge call, no attempt bump, no verdict row."""
    m, read, hook = _wire(monkeypatch, status=status)
    out = await _step(m)
    assert out["verification"] is None and out["outcome"] == "completed"
    hook.assert_not_awaited()


async def test_reply_step_skips_the_status_read_without_a_completed_declaration(
    monkeypatch,
):
    m, read, hook = _wire(monkeypatch, status="in_progress", outcome="needs_input")
    out = await _step(m)
    assert out["verification"] is None
    read.assert_not_awaited()
    hook.assert_not_awaited()
