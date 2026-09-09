"""ConversationsAiStore.append_system_message (phase 2b-1): a system-role row
the history replay reads back as ``system`` — used to carry a compaction
summary into a forked session."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

pytestmark = pytest.mark.unit


async def test_sends_a_system_row_with_the_text_and_meta():
    repo = AsyncMock()
    repo.send_message = AsyncMock(
        return_value={"id": 5, "conversation_id": 200, "created_at": "t"}
    )
    with patch(
        "app.repositories.conversation_repository.get_conversation_repository",
        return_value=repo,
    ):
        out = await ConversationsAiStore().append_system_message(
            session_id=200,
            content="[Earlier conversation summary]\nS",
            metadata={"k": 1},
        )
    repo.send_message.assert_awaited_once_with(
        conversation_id=200,
        sender_id=None,
        sender_type="system",
        type="text",
        body={"text": "[Earlier conversation summary]\nS", "meta": {"k": 1}},
        parent_id=None,
    )
    assert out["role"] == "system" and out["id"] == 5


async def test_reserved_decoration_keys_are_rejected_like_the_assistant_path():
    with pytest.raises(ValueError):
        await ConversationsAiStore().append_system_message(
            session_id=200, content="x", metadata={"agent_id": "no"}
        )
