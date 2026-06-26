"""Unit tests for @-mention fan-out (Task 1: mention_count + user fan-out).

Repo tests:
  - get_my_channels SELECT contains mention_count; returned dicts have the key.
  - list_member_ids queries channel_members and returns user_id strings.

Service tests:
  - post_message fans out to valid mentions only (excludes sender, non-members).
  - post_message with no mention_user_ids: increment_mentions NOT called.
  - post_message with malformed mention_user_ids (bare string): no crash.
  - post_message with malformed mention_user_ids (list of ints): no crash.
  - post_message with content_type != 'text': fan-out skipped entirely.

Mocking patterns mirror test_chat_edit_delete.py:
  - Repo-level: patch("app.db.engine.fetch_all", ...) to capture SQL.
  - Service-level: pass MagicMock(repo=...) to ChatService constructor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.chat_repository import ChatRepository
from app.services.chat_service import ChatService

_REPO = ChatRepository()

_CHAN_ID = 1234567890123456789
_USER_SELF = "U_self"
_USER_BOB = "U_bob"
_USER_OUTSIDER = "U_outsider"

_SENT_ROW = {
    "id": 1,
    "channel_id": _CHAN_ID,
    "seq": 1,
    "sender_id": _USER_SELF,
    "sender_type": "user",
    "content_type": "text",
    "body": {
        "text": "hi @bob",
        "mention_user_ids": [_USER_BOB, _USER_SELF, _USER_OUTSIDER],
    },
    "reply_to_id": None,
    "from_bot_agent_id": None,
    "edited_at": None,
    "deleted_at": None,
    "created_at": "2026-06-26T00:00:00",
}


# ─── Repo-level: get_my_channels ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_my_channels_select_contains_mention_count():
    """get_my_channels SELECT must include cm.mention_count (aliased) so that
    each returned channel dict carries a mention_count key."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        return [
            {
                "id": _CHAN_ID,
                "team_id": 1,
                "type": "team",
                "history_mode": "visible",
                "name": "general",
                "topic": None,
                "last_message_seq": 5,
                "created_at": "2026-06-26T00:00:00",
                "unread": 2,
                "mention_count": 1,
            }
        ]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        rows = await _REPO.get_my_channels("some-user-id")

    sql = captured["sql"]
    assert "mention_count" in sql, "SELECT must include mention_count"
    assert len(rows) == 1
    assert "mention_count" in rows[0], "returned dict must have mention_count key"
    assert rows[0]["mention_count"] == 1


# ─── Repo-level: list_member_ids ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_member_ids_returns_user_id_strings():
    """list_member_ids must SELECT user_id FROM channel_members WHERE channel_id
    and return the results as a list of plain strings."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"user_id": _USER_SELF}, {"user_id": _USER_BOB}]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.list_member_ids(_CHAN_ID)

    sql = captured["sql"]
    assert "channel_members" in sql
    assert "user_id" in sql
    assert result == [_USER_SELF, _USER_BOB]
    # channel_id must be bound as int (bigint coercion)
    assert captured["params"]["cid"] == int(_CHAN_ID)


# ─── Service helpers ──────────────────────────────────────────────────────────


def _make_mock_repo(
    *,
    is_member: bool = True,
    send_result=None,
    member_ids: list[str] | None = None,
) -> MagicMock:
    repo = MagicMock()
    repo.is_member = AsyncMock(return_value=is_member)
    repo.send_message = AsyncMock(
        return_value=send_result if send_result is not None else _SENT_ROW
    )
    repo.list_member_ids = AsyncMock(
        return_value=member_ids if member_ids is not None else [_USER_SELF, _USER_BOB]
    )
    repo.increment_mentions = AsyncMock(return_value=None)
    return repo


# ─── Service-level: mention fan-out ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_message_mention_fanout_filters_correctly():
    """mention_user_ids=[U_bob, U_self, U_outsider], sender=U_self,
    members=[U_self, U_bob] → increment_mentions called exactly once with
    user_ids=[U_bob] (excludes sender + non-member)."""
    repo = _make_mock_repo(member_ids=[_USER_SELF, _USER_BOB])
    svc = ChatService(repo=repo)

    body = {
        "text": "hi @bob",
        "mention_user_ids": [_USER_BOB, _USER_SELF, _USER_OUTSIDER],
    }
    result = await svc.post_message(
        channel_id=_CHAN_ID,
        user_id=_USER_SELF,
        content_type="text",
        body=body,
        reply_to_id=None,
    )

    repo.increment_mentions.assert_awaited_once_with(
        channel_id=_CHAN_ID,
        user_ids=[_USER_BOB],  # only Bob: self excluded, outsider not a member
    )
    assert result == _SENT_ROW


@pytest.mark.asyncio
async def test_post_message_no_mention_ids_skips_fanout():
    """Body without mention_user_ids → increment_mentions must NOT be called."""
    repo = _make_mock_repo()
    svc = ChatService(repo=repo)

    await svc.post_message(
        channel_id=_CHAN_ID,
        user_id=_USER_SELF,
        content_type="text",
        body={"text": "hello world"},
        reply_to_id=None,
    )

    repo.increment_mentions.assert_not_called()


@pytest.mark.asyncio
async def test_post_message_malformed_mention_ids_bare_string():
    """mention_user_ids as a bare string (not a list) → no crash, message is
    still returned, and increment_mentions must NOT be called."""
    repo = _make_mock_repo()
    svc = ChatService(repo=repo)

    result = await svc.post_message(
        channel_id=_CHAN_ID,
        user_id=_USER_SELF,
        content_type="text",
        body={"text": "hi", "mention_user_ids": "U_bob"},  # bare str, not list
        reply_to_id=None,
    )

    repo.increment_mentions.assert_not_called()
    assert result == _SENT_ROW


@pytest.mark.asyncio
async def test_post_message_malformed_mention_ids_list_with_ints():
    """mention_user_ids=[1, 2, 3] (ints, not strs) → all items are filtered out
    as non-str, no crash, message is still returned, increment_mentions NOT called."""
    repo = _make_mock_repo()
    svc = ChatService(repo=repo)

    result = await svc.post_message(
        channel_id=_CHAN_ID,
        user_id=_USER_SELF,
        content_type="text",
        body={"text": "hi", "mention_user_ids": [1, 2, 3]},  # ints, not strs
        reply_to_id=None,
    )

    repo.increment_mentions.assert_not_called()
    assert result == _SENT_ROW


@pytest.mark.asyncio
async def test_post_message_non_text_content_type_skips_fanout():
    """Fan-out only runs when content_type='text'; other types must skip it."""
    repo = _make_mock_repo()
    svc = ChatService(repo=repo)

    await svc.post_message(
        channel_id=_CHAN_ID,
        user_id=_USER_SELF,
        content_type="image",  # not 'text'
        body={"url": "http://example.com/img.jpg", "mention_user_ids": [_USER_BOB]},
        reply_to_id=None,
    )

    repo.increment_mentions.assert_not_called()
