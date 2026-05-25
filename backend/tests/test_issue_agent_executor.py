"""run_issue_agent delegates to the full chat runtime via run_session_turn."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_run_issue_agent_passes_chunk_callback_and_publishes(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    captured = {}

    async def fake_run_session_turn(
        session_id, *, user_id, content, trigger, chunk_callback=None, **kw
    ):
        captured["has_cb"] = chunk_callback is not None
        if chunk_callback:
            await chunk_callback("tok")
        return {
            "assistant_message": {
                "id": "m2",
                "role": "assistant",
                "content": "result",
                "agent_id": None,
                "metadata_json": {},
                "created_at": "2026-05-25T00:00:00+00:00",
            }
        }

    fake_chat = type(
        "C", (), {"run_session_turn": staticmethod(fake_run_session_turn)}
    )()
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-99")
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)

    chunks, messages = [], []
    monkeypatch.setattr(
        m, "publish_chunk", AsyncMock(side_effect=lambda iid, d: chunks.append(d))
    )
    monkeypatch.setattr(
        m,
        "publish_message",
        AsyncMock(side_effect=lambda iid, row, **k: messages.append(row)),
    )
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    out = await m.run_issue_agent(
        issue={"id": 42, "title": "do thing", "description": "details"},
        agent_id="a",
        user_id="u",
    )
    assert out == "result"
    assert captured["has_cb"] is True
    assert chunks == ["tok"]
    assert messages and messages[0]["content"] == "result"


async def test_run_issue_agent_runs_session_turn(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "essay"}, "run_id": "r"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
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
