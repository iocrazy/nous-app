"""Tests for GET /issues/{issue_id}/messages — dual-path (session vs legacy).

Task 5 (Spec-1a): when an issue has ai_session_id set, the endpoint reads
ai_messages and maps them to IssueMessage shape. When ai_session_id is None
it falls back to the legacy issue_messages table.
"""

from __future__ import annotations

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


# ─── path A: ai_session present ─────────────────────────────────────────


async def test_session_path_user_message_maps_to_comment():
    """A user ai_message maps to kind='comment' with author_user_id from session."""
    issue_id = 42
    session_id = uuid4()
    msg_id = uuid4()
    session_user_id = uuid4()

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": str(session_id), "user_id": str(session_user_id)}

    ai_messages = [
        {
            "id": str(msg_id),
            "session_id": str(session_id),
            "role": "user",
            "content": "Hello agent",
            "agent_id": None,
            "metadata_json": {},
            "prompt_tokens": 10,
            "completion_tokens": 0,
            "created_at": _dt(),
        }
    ]

    def _tables(tbl):
        if tbl == "ai_sessions":
            return _chain_builder(session_row)
        if tbl == "ai_messages":
            return _chain_builder(ai_messages)
        raise AssertionError(f"unexpected table access: {tbl}")

    sb = _make_sb_client(_tables)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.get_async_supabase_admin",
            AsyncMock(return_value=sb),
        ),
    ):
        auth = _make_auth(user_id=session_user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert str(msg.id) == str(msg_id)
    assert msg.issue_id == issue_id
    assert msg.kind.value == "comment"
    assert msg.author_user_id == session_user_id
    assert msg.author_agent_id is None
    assert msg.body == "Hello agent"


async def test_session_path_assistant_message_maps_to_agent_run():
    """An assistant ai_message maps to kind='agent_run' with author_agent_id."""
    issue_id = 42
    session_id = uuid4()
    msg_id = uuid4()
    agent_uuid = uuid4()
    # agent_runs.id is a BIGINT Snowflake since mig 232 → numeric string.
    run_bigint = "310819108761487"
    session_user_id = uuid4()

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": str(session_id), "user_id": str(session_user_id)}

    ai_messages = [
        {
            "id": str(msg_id),
            "session_id": str(session_id),
            "role": "assistant",
            "content": "I can help with that",
            "agent_id": str(agent_uuid),
            "metadata_json": {"run_id": run_bigint},
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "created_at": _dt(),
        }
    ]

    def _tables(tbl):
        if tbl == "ai_sessions":
            return _chain_builder(session_row)
        if tbl == "ai_messages":
            return _chain_builder(ai_messages)
        raise AssertionError(f"unexpected table access: {tbl}")

    sb = _make_sb_client(_tables)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.get_async_supabase_admin",
            AsyncMock(return_value=sb),
        ),
    ):
        auth = _make_auth(user_id=session_user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert msg.kind.value == "agent_run"
    assert msg.author_agent_id == agent_uuid
    assert msg.author_user_id is None
    assert msg.body == "I can help with that"
    assert msg.agent_run_id == run_bigint


async def test_session_path_system_message_maps_to_system_status():
    """A system ai_message maps to kind='system_status' with from/to_status."""
    issue_id = 42
    session_id = uuid4()
    msg_id = uuid4()
    session_user_id = uuid4()

    issue_row = _fake_issue(ai_session_id=session_id, user_id=session_user_id)
    session_row = {"id": str(session_id), "user_id": str(session_user_id)}

    ai_messages = [
        {
            "id": str(msg_id),
            "session_id": str(session_id),
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
            "created_at": _dt(),
        }
    ]

    def _tables(tbl):
        if tbl == "ai_sessions":
            return _chain_builder(session_row)
        if tbl == "ai_messages":
            return _chain_builder(ai_messages)
        raise AssertionError(f"unexpected table access: {tbl}")

    sb = _make_sb_client(_tables)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.get_async_supabase_admin",
            AsyncMock(return_value=sb),
        ),
    ):
        auth = _make_auth(user_id=session_user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
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

    legacy_builder = _chain_builder(legacy_rows)

    def _tables(tbl):
        if tbl == "issue_messages":
            return legacy_builder
        # ai_sessions / ai_messages must NOT be accessed in the legacy path
        raise AssertionError(f"unexpected table access in legacy path: {tbl}")

    sb = _make_sb_client(_tables)

    with (
        patch(
            "app.api.issue_messages_router.issue_repository.get_by_id",
            AsyncMock(return_value=issue_row),
        ),
        patch(
            "app.api.issue_messages_router.get_async_supabase_admin",
            AsyncMock(return_value=sb),
        ),
    ):
        auth = _make_auth(user_id=user_id)
        result = await list_issue_messages(issue_id, auth)

    assert result.total == 1
    msg = result.messages[0]
    assert str(msg.id) == str(msg_id)
    assert msg.kind.value == "comment"
    assert msg.body == "Legacy comment"
    # Verify the legacy table was queried with the right issue_id filter
    legacy_builder.eq.assert_any_call("issue_id", issue_id)
