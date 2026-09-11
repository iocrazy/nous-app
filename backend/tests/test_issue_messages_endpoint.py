"""Tests for GET /issues/{issue_id}/messages — dual-path (session vs legacy).

Task 5 (Spec-1a): when an issue has ai_session_id set, the endpoint reads
the session's messages. Fix-up (Conversations Phase 3, Task 6 review):
the session path was repointed from the retired ``ai_sessions``/
``ai_messages`` tables onto ``ConversationsAiStore`` — mig 333 (same PR)
drops the legacy tables, so a test still mocking ``sb.table("ai_sessions")``
would pass for the wrong reason (it never exercised the real read path).
These tests now mock ``ConversationsAiStore`` directly, with REALISTIC
BIGINT snowflake ids (not convenient UUID-shaped strings) and real
``datetime`` timestamps — the actual shape ``ConversationsAiStore``
hands back — per the project lesson that type-homogeneous fakes mask
cross-store id-type bugs (this is what let the UUID()-on-bigint crash
ship in the first place).

When ai_session_id is None the endpoint still falls back to the legacy
``issue_messages`` table (a distinct, still-live table — unaffected by
this fix-up).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.api.issue_messages_router import list_issue_messages

# ─── helpers ────────────────────────────────────────────────────────────


def _dt(ts: str = "2024-01-01T00:00:00+00:00") -> str:
    return ts


def _fake_issue(ai_session_id=None, user_id=None):
    uid = str(user_id or uuid4())
    return {
        "id": 42,
        "created_by_user_id": uid,
        "assignee_user_id": None,
        "ai_session_id": str(ai_session_id) if ai_session_id else None,
    }


def _make_auth(user_id=None):
    auth = MagicMock()
    auth.user_id = user_id or uuid4()
    return auth


def _make_sb_client(table_side_effect):
    """Build a synchronous MagicMock supabase client whose .table() dispatches
    to *table_side_effect*. The .execute() on each builder is an AsyncMock so
    that ``await builder.execute()`` works.

    get_async_supabase_admin() is an async function that returns the client, so
    the patch wraps it as ``AsyncMock(return_value=sb)``."""
    sb = MagicMock()
    sb.table.side_effect = table_side_effect
    return sb


def _chain_builder(data, count=None):
    """Return a fluent builder mock whose .execute() resolves to (data, count)."""
    b = MagicMock()
    b.select.return_value = b
    b.eq.return_value = b
    b.order.return_value = b
    b.maybe_single.return_value = b
    b.execute = AsyncMock(
        return_value=MagicMock(
            data=data, count=count or len(data) if isinstance(data, list) else 1
        )
    )
    return b


def _fake_conversations_store(session_row, message_rows):
    """Stand in for ConversationsAiStore, returning pre-canned legacy-shaped
    rows from get_session/get_messages exactly as the real store would
    (BIGINT ids un-stringified, datetime objects for created_at)."""
    store = MagicMock()
    store.get_session = AsyncMock(return_value=session_row)
    store.get_messages = AsyncMock(return_value=message_rows)
    return store


# ─── path A: ai_session present (ConversationsAiStore) ──────────────────


async def test_session_path_user_message_maps_to_comment():
    """A user message row maps to kind='comment' with author_user_id from
    the session, reading through ConversationsAiStore with a realistic
    BIGINT snowflake message id + a real datetime created_at."""
    issue_id = 42
    session_id = 987654321012345  # realistic BIGINT snowflake, not a UUID
    msg_id = 323848780659604  # realistic BIGINT snowflake
    session_user_id = uuid4()
    created_at = datetime.now(timezone.utc)

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": session_id, "user_id": str(session_user_id)}

    message_rows = [
        {
            "id": msg_id,
            "session_id": session_id,
            "role": "user",
            "content": "Hello agent",
            "agent_id": None,
            "metadata_json": {},
            "prompt_tokens": 10,
            "completion_tokens": 0,
            "created_at": created_at,
        }
    ]

    store = _fake_conversations_store(session_row, message_rows)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.ConversationsAiStore",
            return_value=store,
        ),
    ):
        auth = _make_auth(user_id=session_user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert msg.id == str(msg_id)  # str, never UUID-parsed
    assert msg.issue_id == issue_id
    assert msg.kind.value == "comment"
    assert msg.author_user_id == session_user_id
    assert msg.author_agent_id is None
    assert msg.body == "Hello agent"
    store.get_session.assert_awaited_once_with(session_id=session_id)
    store.get_messages.assert_awaited_once()


async def test_session_path_assistant_message_maps_to_agent_run():
    """An assistant message row maps to kind='agent_run' with author_agent_id,
    reading through ConversationsAiStore with realistic BIGINT ids."""
    issue_id = 42
    session_id = 555000111222333
    msg_id = 323848780659604
    agent_uuid = uuid4()
    # agent_runs.id is a BIGINT Snowflake since mig 232 → numeric string.
    run_bigint = "310819108761487"
    session_user_id = uuid4()
    created_at = datetime.now(timezone.utc)

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": session_id, "user_id": str(session_user_id)}

    message_rows = [
        {
            "id": msg_id,
            "session_id": session_id,
            "role": "assistant",
            "content": "I can help with that",
            "agent_id": str(agent_uuid),
            "metadata_json": {"run_id": run_bigint},
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "created_at": created_at,
        }
    ]

    store = _fake_conversations_store(session_row, message_rows)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.ConversationsAiStore",
            return_value=store,
        ),
    ):
        auth = _make_auth(user_id=session_user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert msg.id == str(msg_id)
    assert msg.kind.value == "agent_run"
    assert msg.author_agent_id == agent_uuid
    assert msg.author_user_id is None
    assert msg.body == "I can help with that"
    assert msg.agent_run_id == run_bigint


async def test_session_path_system_message_maps_to_system_status():
    """A system message row maps to kind='system_status' with from/to_status,
    reading through ConversationsAiStore with a realistic BIGINT id."""
    issue_id = 42
    session_id = 444555666777888
    msg_id = 323848780659604
    session_user_id = uuid4()
    created_at = datetime.now(timezone.utc)

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": session_id, "user_id": str(session_user_id)}

    message_rows = [
        {
            "id": msg_id,
            "session_id": session_id,
            "role": "system",
            "content": "Status changed",
            "agent_id": None,
            "metadata_json": {
                "kind": "status",
                "from": "backlog",
                "to": "in_progress",
            },
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "created_at": created_at,
        }
    ]

    store = _fake_conversations_store(session_row, message_rows)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.ConversationsAiStore",
            return_value=store,
        ),
    ):
        auth = _make_auth(user_id=session_user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert msg.id == str(msg_id)
    assert msg.kind.value == "system_status"
    assert msg.from_status == "backlog"
    assert msg.to_status == "in_progress"


# ─── path B: no ai_session — legacy fallback ────────────────────────────


async def test_legacy_path_reads_issue_messages_when_no_session():
    """When ai_session_id is None, the endpoint reads issue_messages (legacy)."""
    issue_id = 42
    msg_id = uuid4()
    user_id = uuid4()

    issue_row = _fake_issue(ai_session_id=None, user_id=user_id)

    legacy_rows = [
        {
            "id": str(msg_id),
            "issue_id": issue_id,
            "kind": "comment",
            "author_user_id": str(user_id),
            "author_agent_id": None,
            "body": "Legacy comment",
            "meta": {},
            "duration_seconds": None,
            "agent_run_id": None,
            "from_status": None,
            "to_status": None,
            "created_at": _dt(),
        }
    ]

    # The legacy path reads issue_messages via the ORM session and validates
    # each row with from_attributes, so hand it ORM-like objects.
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    objs = [SimpleNamespace(**row) for row in legacy_rows]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return objs

    captured = {}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["stmt"] = stmt
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    import app.db.session as dbs

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch.object(dbs, "read_scope", _scope),
    ):
        auth = _make_auth(user_id=user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert str(msg.id) == str(msg_id)
    assert msg.kind.value == "comment"
    assert msg.body == "Legacy comment"
    # Verify the legacy table was queried with the right issue_id filter
    assert (
        issue_id
        in captured["stmt"].compile(dialect=postgresql.dialect()).params.values()
    )


# ─── attachments on the read path (3a Task 8a, defect 1) ────────────────


async def test_session_path_returns_the_stored_output_ref_attachment():
    """Real-stack defect: POST stored the citation, GET never handed it back,
    so the chip could not survive a reload. The row shape here is exactly what
    ``ConversationsAiStore._to_legacy_message_shape`` returns (``attachments``
    straight off ``body``), with the ids as the store delivers them."""
    issue_id = 348392006624870
    session_id = 987654321012345
    session_user_id = uuid4()

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": session_id, "user_id": str(session_user_id)}
    message_rows = [
        {
            "id": 323848780659604,
            "session_id": session_id,
            "role": "user",
            "content": "compare this with the new one",
            "agent_id": None,
            "metadata_json": None,
            "attachments": [
                {
                    "kind": "output_ref",
                    "ref_kind": "script_shot",
                    "ref_id": "337650953731886",
                    "version": 1,
                    "title": "MEDIUM",
                }
            ],
            "created_at": datetime.now(timezone.utc),
        }
    ]

    store = _fake_conversations_store(session_row, message_rows)
    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.ConversationsAiStore",
            return_value=store,
        ),
    ):
        result = await list_issue_messages(issue_id, _make_auth(session_user_id))

    wire = result.model_dump()["messages"][0]["attachments"][0]
    assert wire == {
        "kind": "output_ref",
        "ref_kind": "script_shot",
        "ref_id": "337650953731886",
        "version": 1,
        "title": "MEDIUM",
        # the read model's other keys are present-and-null, never invented
        "resource_id": None,
        "asset_id": None,
        "loadout_id": None,
        "mime": None,
        "alt_text": None,
        "name": None,
    }


async def test_session_path_row_without_attachments_reads_back_as_null():
    """Every message predating 3a has no ``attachments`` key at all; the
    endpoint must answer ``null`` (the chosen convention) rather than fail."""
    issue_id = 42
    session_id = 987654321012345
    session_user_id = uuid4()

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": session_id, "user_id": str(session_user_id)}
    message_rows = [
        {
            "id": 323848780659605,
            "session_id": session_id,
            "role": "user",
            "content": "old comment",
            "agent_id": None,
            "metadata_json": {},
            "created_at": datetime.now(timezone.utc),
        }
    ]

    store = _fake_conversations_store(session_row, message_rows)
    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.ConversationsAiStore",
            return_value=store,
        ),
    ):
        result = await list_issue_messages(issue_id, _make_auth(session_user_id))

    assert result.messages[0].attachments is None
    assert result.model_dump()["messages"][0]["attachments"] is None


async def test_legacy_table_rows_still_deserialize_without_attachments():
    """``issue_messages`` has no such column — a row from it must validate
    against the widened read model unchanged."""
    from types import SimpleNamespace

    from app.schemas.issue_message import IssueMessage

    obj = SimpleNamespace(
        id=str(uuid4()),
        issue_id=42,
        kind="comment",
        author_user_id=str(uuid4()),
        author_agent_id=None,
        body="Legacy comment",
        meta={},
        duration_seconds=None,
        agent_run_id=None,
        from_status=None,
        to_status=None,
        created_at=_dt(),
    )
    assert IssueMessage.model_validate(obj, from_attributes=True).attachments is None
