"""run_issue_reply_step: same hook, same result key."""

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests

USER = "22222222-2222-2222-2222-222222222222"


async def test_reply_step_verifies_a_completed_declaration(monkeypatch):
    from app.workflows import issue_lifecycle as m

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
            "tool_calls": [
                {
                    "name": "FinishIssue",
                    "args": {"outcome": "completed"},
                    "result": {"ok": True, "outcome": "completed"},
                }
            ],
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    hook = AsyncMock(
        return_value=("completed", None, {"verdict": "pass", "attempt": 1})
    )
    monkeypatch.setattr(m, "apply_completion_verification", hook)
    out = await m.run_issue_reply_step.__wrapped__(
        issue_id=7, session_id="123", user_id=USER, reply_text="hi"
    )
    assert out["verification"] == {"verdict": "pass", "attempt": 1}
    kw = hook.await_args.kwargs
    assert (
        kw["trigger"] == "issue_reply"
        and kw["issue_id"] == 7
        and kw["result"]["run_id"] == "r9"
    )
