"""Regression tests for two prod bugs found by live E2E (2026-06-29) that the
mocked unit tests missed because they never exercised real asyncpg types:

  1. MessageOut(sender_id=<uuid.UUID>) → ResponseValidationError (500) on every
     message endpoint, because asyncpg returns auth.users UUID as a uuid.UUID
     object but MessageOut.sender_id is str. Fixed via a before-validator.
  2. list_messages with before_seq=None → asyncpg AmbiguousParameterError
     ("could not determine data type of parameter $2") because the NULL bind in
     ``(:before IS NULL OR seq < :before)`` had no inferable type. Fixed with
     CAST(:before AS bigint).
"""

import uuid
from datetime import datetime, timezone

from app.schemas.chat import MessageOut


def test_messageout_coerces_uuid_sender_id_to_str():
    """A uuid.UUID sender_id (as asyncpg returns it) must coerce to str, not raise."""
    uid = uuid.uuid4()
    m = MessageOut(
        id=1,
        channel_id=2,
        seq=3,
        sender_id=uid,  # asyncpg hands back a uuid.UUID here
        sender_type="user",
        content_type="text",
        body={"text": "hi"},
        created_at=datetime.now(timezone.utc),
    )
    assert m.sender_id == str(uid)
    assert isinstance(m.sender_id, str)


def test_messageout_none_sender_id_stays_none():
    """Agent messages have sender_id=None — must remain None, not 'None'."""
    m = MessageOut(
        id=1,
        channel_id=2,
        seq=3,
        sender_id=None,
        sender_type="agent",
        content_type="text",
        body={"text": "hi"},
        created_at=datetime.now(timezone.utc),
    )
    assert m.sender_id is None


def test_messageout_str_sender_id_unchanged():
    """A plain string sender_id passes through unchanged."""
    m = MessageOut(
        id=1,
        channel_id=2,
        seq=3,
        sender_id="abc-123",
        sender_type="user",
        content_type="text",
        body={},
        created_at=datetime.now(timezone.utc),
    )
    assert m.sender_id == "abc-123"


# ── list_messages NULL-bind cast (bug 2) ──────────────────────────────────────


def test_list_messages_casts_before_param_to_bigint():
    """The before_seq bind must be CAST to bigint so asyncpg can type the NULL
    case (before_seq=None) instead of raising AmbiguousParameterError."""
    import asyncio
    from unittest.mock import patch

    from app.repositories.chat_repository import ChatRepository

    captured = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    repo = ChatRepository()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        asyncio.run(repo.list_messages(channel_id=1, before_seq=None, limit=30))

    sql = captured["sql"]
    assert "CAST(:before AS bigint)" in sql, (
        "before_seq must be CAST to bigint to avoid asyncpg "
        "AmbiguousParameterError on the NULL case"
    )
    # the raw, un-cast ambiguous form must be gone
    assert ":before IS NULL OR seq < :before" not in sql
