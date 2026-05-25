"""run_issue_agent delegates to the full chat runtime via run_session_turn."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


async def test_run_issue_agent_runs_session_turn(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(m, "get_or_create_issue_session", AsyncMock(return_value="sess-1"))
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "essay"}, "run_id": "r"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    out = await m.run_issue_agent(
        issue={"id": 409, "title": "写一篇短文", "description": "春"},
        agent_id="a",
        user_id="u",
    )
    chat_svc.run_session_turn.assert_awaited_once()
    aa = chat_svc.run_session_turn.await_args
    assert aa.kwargs["trigger"] == "issue_dispatch"
    assert "写一篇短文" in str(aa)  # issue task is in the content arg
    assert out == "essay"


async def test_run_issue_agent_raises_when_no_session(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(m, "get_or_create_issue_session", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError):
        await m.run_issue_agent(
            issue={"id": 1, "title": "t", "description": None},
            agent_id="a",
            user_id="u",
        )
