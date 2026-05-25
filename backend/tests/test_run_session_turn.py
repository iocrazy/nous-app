"""run_session_turn is the shared turn core; chat() delegates to it unchanged."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException


async def test_chat_delegates_to_run_session_turn(monkeypatch):
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    svc = AILibraryChatService()
    svc.get_session = AsyncMock(return_value={"agent_slug": "writer"})
    sentinel = {
        "assistant_message": {"role": "assistant", "content": "hi"},
        "run_id": "r1",
        "usage": {},
        "tool_calls": [],
    }
    svc.run_session_turn = AsyncMock(return_value=sentinel)
    session_id = uuid4()
    user_id = uuid4()
    out = await svc.chat(session_id, user_id=user_id, content="hello")
    svc.run_session_turn.assert_awaited_once()
    assert out == sentinel


async def test_chat_guards_missing_agent_slug(monkeypatch):
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    svc = AILibraryChatService()
    svc.get_session = AsyncMock(return_value={"agent_slug": None})
    with pytest.raises(HTTPException):
        await svc.chat(uuid4(), user_id=uuid4(), content="x")
