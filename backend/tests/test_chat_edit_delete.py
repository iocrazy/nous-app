"""Unit tests for ChatRepository.edit_message + soft_delete_message (Task 1).

Mocks app.db.engine.execute_returning_one to capture SQL + bound params without
a real database — following the pattern in test_ai_transcription_sql.py and
test_generated_media_register.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.repositories.chat_repository import ChatRepository

_REPO = ChatRepository()

# Snowflake IDs as strings — tests the _bigint coercion path
_CHAN_ID_STR = "1234567890123456789"
_MSG_ID_STR = "9876543210987654321"
_SENDER_ID = "user-uuid-abc"
_BODY = {"text": "hello edited"}

_RETURNED_ROW = {
    "id": int(_MSG_ID_STR),
    "channel_id": int(_CHAN_ID_STR),
    "seq": 3,
    "sender_id": _SENDER_ID,
    "sender_type": "user",
    "content_type": "text",
    "body": _BODY,
    "reply_to_id": None,
    "from_bot_agent_id": None,
    "edited_at": "2026-06-26T00:00:00",
    "deleted_at": None,
    "created_at": "2026-06-25T23:00:00",
}


# ─── edit_message ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_message_sql_has_ownership_gate_and_returns_row():
    """edit_message issues an UPDATE that sets body + edited_at, gates on
    sender_id / sender_type='user' / content_type='text' / deleted_at IS NULL,
    and returns the row dict on hit."""
    captured: dict = {}

    async def fake_returning_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return _RETURNED_ROW

    with patch("app.db.engine.execute_returning_one", fake_returning_one):
        result = await _REPO.edit_message(
            channel_id=int(_CHAN_ID_STR),
            message_id=int(_MSG_ID_STR),
            sender_id=_SENDER_ID,
            body=_BODY,
        )

    sql = captured["sql"]
    # Must be an UPDATE on channel_messages
    assert "UPDATE" in sql
    assert "channel_messages" in sql
    # Must SET body and edited_at
    assert "body" in sql
    assert "edited_at" in sql
    # Ownership + state gate
    assert "sender_id = :sender_id" in sql
    assert "sender_type = 'user'" in sql
    assert "content_type = 'text'" in sql
    assert "deleted_at IS NULL" in sql
    # RETURNING must include the full column set
    assert "from_bot_agent_id" in sql
    # Return value must be the row dict
    assert result == _RETURNED_ROW


@pytest.mark.asyncio
async def test_edit_message_returns_none_on_miss():
    """edit_message returns None when the driver returns no row (caller is not
    the owner, message is already deleted, or content_type != 'text')."""

    async def fake_returning_one(sql, params=None):
        return None

    with patch("app.db.engine.execute_returning_one", fake_returning_one):
        result = await _REPO.edit_message(
            channel_id=1,
            message_id=2,
            sender_id=_SENDER_ID,
            body=_BODY,
        )

    assert result is None


@pytest.mark.asyncio
async def test_edit_message_bigint_coercion():
    """String snowflake IDs passed to edit_message must be coerced to int
    before being bound as params (asyncpg int8 codec is strict)."""
    captured: dict = {}

    async def fake_returning_one(sql, params=None):
        captured["params"] = params
        return _RETURNED_ROW

    with patch("app.db.engine.execute_returning_one", fake_returning_one):
        await _REPO.edit_message(
            channel_id=_CHAN_ID_STR,  # pass as string
            message_id=_MSG_ID_STR,  # pass as string
            sender_id=_SENDER_ID,
            body=_BODY,
        )

    params = captured["params"]
    assert isinstance(params["cid"], int), "channel_id must be coerced to int"
    assert isinstance(params["mid"], int), "message_id must be coerced to int"
    assert params["cid"] == int(_CHAN_ID_STR)
    assert params["mid"] == int(_MSG_ID_STR)


# ─── soft_delete_message ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_delete_message_sql_sets_deleted_at_and_returns_row():
    """soft_delete_message issues an UPDATE that sets deleted_at = now(), gates on
    sender_id / sender_type='user' / deleted_at IS NULL, and returns the row dict.
    Unlike edit_message, it must NOT restrict content_type (any own user message
    can be soft-deleted)."""
    captured: dict = {}

    _DELETED_ROW = {**_RETURNED_ROW, "deleted_at": "2026-06-26T01:00:00"}

    async def fake_returning_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return _DELETED_ROW

    with patch("app.db.engine.execute_returning_one", fake_returning_one):
        result = await _REPO.soft_delete_message(
            channel_id=int(_CHAN_ID_STR),
            message_id=int(_MSG_ID_STR),
            sender_id=_SENDER_ID,
        )

    sql = captured["sql"]
    # Must be an UPDATE on channel_messages
    assert "UPDATE" in sql
    assert "channel_messages" in sql
    # Must SET deleted_at
    assert "deleted_at" in sql
    # Ownership + state gate
    assert "sender_id = :sender_id" in sql
    assert "sender_type = 'user'" in sql
    assert "deleted_at IS NULL" in sql
    # Must NOT restrict to text messages — any own message can be deleted
    assert "content_type = 'text'" not in sql
    # RETURNING must include the full column set
    assert "from_bot_agent_id" in sql
    # Return value is the row dict
    assert result == _DELETED_ROW


@pytest.mark.asyncio
async def test_soft_delete_message_returns_none_on_miss():
    """soft_delete_message returns None when no row matches the ownership gate
    (not the sender, or message already deleted)."""

    async def fake_returning_one(sql, params=None):
        return None

    with patch("app.db.engine.execute_returning_one", fake_returning_one):
        result = await _REPO.soft_delete_message(
            channel_id=1,
            message_id=2,
            sender_id=_SENDER_ID,
        )

    assert result is None


@pytest.mark.asyncio
async def test_soft_delete_message_bigint_coercion():
    """String snowflake IDs passed to soft_delete_message must be coerced to int."""
    captured: dict = {}

    async def fake_returning_one(sql, params=None):
        captured["params"] = params
        return _RETURNED_ROW

    with patch("app.db.engine.execute_returning_one", fake_returning_one):
        await _REPO.soft_delete_message(
            channel_id=_CHAN_ID_STR,  # string
            message_id=_MSG_ID_STR,  # string
            sender_id=_SENDER_ID,
        )

    params = captured["params"]
    assert isinstance(params["cid"], int), "channel_id must be coerced to int"
    assert isinstance(params["mid"], int), "message_id must be coerced to int"
    assert params["cid"] == int(_CHAN_ID_STR)
    assert params["mid"] == int(_MSG_ID_STR)
