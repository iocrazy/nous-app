"""The session view shows the NEWEST 200 messages, not the first 200.

``GET /ai-library/sessions/{id}`` fed the chat panel the oldest ``limit``
messages, so a conversation past 200 messages opened on its beginning and the
latest turns were unreachable (production had one such conversation at 602
messages). Opening a chat means seeing where it left off; the endpoint now asks
the service for the newest window. Paging back to earlier history is a
separate ticket.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import UUID

import pytest

r = importlib.import_module("app.api.ai_library_router")

pytestmark = pytest.mark.unit


async def test_session_view_asks_for_the_newest_window(monkeypatch):
    seen: dict = {}

    async def _get_session(self, session_id, *, user_id):
        return {"id": session_id, "title": "t"}

    async def _get_messages(self, session_id, *, user_id, limit=200, newest=False):
        seen.update({"limit": limit, "newest": newest})
        return []

    monkeypatch.setattr(r.AILibraryChatService, "get_session", _get_session)
    monkeypatch.setattr(r.AILibraryChatService, "get_messages", _get_messages)
    auth = SimpleNamespace(user_id=str(UUID(int=7)))
    out = await r.get_chat_session("123", auth)
    assert out["messages"] == []
    assert seen == {"limit": 200, "newest": True}, seen
