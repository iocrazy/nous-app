"""Unit + integration tests for ConversationsAiStore (Task 3 — session half).

Unit tests (default, no DB): capture SQL/params via the fake-engine harness
copied from ``test_conversation_repository.py`` (transactional ``eng.begin()``
calls) plus direct patches of the ``app.db.engine.fetch_one`` /
``fetch_all`` / ``execute`` module-level helpers (single-statement reads
and writes) — the same transport ``ConversationRepository`` uses.

Integration test (skippable): requires ``INTEGRATION_DATABASE_URL``,
mirroring the ``conv_for_smoke`` pattern in ``test_conversation_repository.py``.

Minimum assertions (brief §Step 1):
  - create_session resolves personal team when team_id is None; ValueError
    from ``_resolve_personal_team_id`` propagates untouched
  - create_session inserts conversation + 2 members + meta in ONE txn
  - list_sessions SQL: type='direct_agent', archived_at IS NULL, user
    member join, optional agent_slug/project_id filters, ORDER BY
    m.updated_at DESC
  - get_session returns None for a missing/archived row
  - bump_counters writes the given ABSOLUTE values (not a SQL increment)
  - every returned dict carries the legacy ai_sessions keys
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

# ── Fake engine helpers (copied from test_conversation_repository.py) ──────────


class _MappingResult:
    """Mimics a SQLAlchemy result that supports .mappings().one()/.first()/.all()."""

    def __init__(self, d: dict) -> None:
        self._d = d

    def mappings(self) -> "_MappingResult":  # noqa: D102
        return self

    def one(self) -> dict:  # noqa: D102
        return self._d

    def first(self) -> dict:  # noqa: D102
        return self._d

    def all(self) -> list[dict]:  # noqa: D102
        return [self._d]


class _FakeConn:
    """Records every execute() call and returns queued results in FIFO order."""

    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self._i = 0
        self.calls: list[dict] = []

    async def execute(self, stmt: Any, params: dict | None = None) -> Any:  # noqa: D102
        self.calls.append({"sql": str(stmt), "params": params or {}})
        result = self._results[self._i]
        self._i += 1
        return result


class _FakeEngine:
    """Minimal SQLAlchemy async engine stub that wraps a _FakeConn."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def begin(self) -> Any:  # noqa: D102
        @asynccontextmanager
        async def _ctx() -> Any:
            yield self._conn

        return _ctx()


def _make_engine(*results: Any) -> tuple[_FakeEngine, _FakeConn]:
    conn = _FakeConn(*results)
    return _FakeEngine(conn), conn


# ── Constants ────────────────────────────────────────────────────────────────

_CONV_ID = 285274231427073
_USER_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"
_AGENT_ID = "9b1f3a30-1111-4a2b-8c3d-abcdef123456"
_AGENT_SLUG = "script_ai"
_TEAM_ID = 900000000000001

_CONV_ROW = {
    "id": _CONV_ID,
    "scope_id": _TEAM_ID,
    "project_id": None,
    "title": "New Chat",
    "created_at": "2026-07-03T00:00:00",
}
_META_ROW = {
    "total_tokens": 0,
    "message_count": 0,
    "updated_at": "2026-07-03T00:00:00",
}
_JOINED_ROW = {
    "id": _CONV_ID,
    "scope_id": _TEAM_ID,
    "project_id": None,
    "title": "New Chat",
    "created_at": "2026-07-03T00:00:00",
    "agent_slug": _AGENT_SLUG,
    "agent_id": _AGENT_ID,
    "total_tokens": 12,
    "message_count": 3,
    "context_type": "script",
    "context_id": "abc",
    "updated_at": "2026-07-03T01:00:00",
    "user_id": _USER_ID,
}

_LEGACY_KEYS = {
    "id",
    "user_id",
    "agent_id",
    "agent_slug",
    "title",
    "status",
    "total_tokens",
    "message_count",
    "project_id",
    "team_id",
    "context_type",
    "context_id",
    "created_at",
    "updated_at",
}


def _store():
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    return ConversationsAiStore()


# ── create_session ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_resolves_personal_team_when_team_id_none() -> None:
    """team_id=None must call _resolve_personal_team_id and use its result as scope_id."""
    store = _store()
    eng, conn = _make_engine(
        _MappingResult(_CONV_ROW),
        None,  # user member insert
        None,  # agent member insert
        _MappingResult(_META_ROW),
    )

    async def fake_resolve(user_id: str) -> str:
        assert user_id == _USER_ID
        return str(_TEAM_ID)

    with (
        patch("app.db.engine.get_engine", return_value=eng),
        patch(
            "app.services.ai.chat.conversations_ai_store._resolve_personal_team_id",
            fake_resolve,
        ),
    ):
        result = await store.create_session(
            user_id=_USER_ID,
            agent_slug=_AGENT_SLUG,
            agent_id=_AGENT_ID,
            title="New Chat",
            project_id=None,
            team_id=None,
            context_type=None,
            context_id=None,
        )

    insert_sql = conn.calls[0]["sql"]
    assert conn.calls[0]["params"]["scope_id"] == _TEAM_ID
    assert "conversations" in insert_sql
    assert result["team_id"] == _TEAM_ID


@pytest.mark.asyncio
async def test_create_session_uses_explicit_team_id_without_resolving() -> None:
    """An explicit team_id must skip the personal-team resolver entirely."""
    store = _store()
    eng, conn = _make_engine(
        _MappingResult(_CONV_ROW),
        None,
        None,
        _MappingResult(_META_ROW),
    )

    async def fail_resolve(user_id: str) -> str:
        raise AssertionError(
            "_resolve_personal_team_id must not be called when team_id given"
        )

    with (
        patch("app.db.engine.get_engine", return_value=eng),
        patch(
            "app.services.ai.chat.conversations_ai_store._resolve_personal_team_id",
            fail_resolve,
        ),
    ):
        await store.create_session(
            user_id=_USER_ID,
            agent_slug=_AGENT_SLUG,
            agent_id=_AGENT_ID,
            title="New Chat",
            project_id=None,
            team_id=_TEAM_ID,
            context_type=None,
            context_id=None,
        )

    assert conn.calls[0]["params"]["scope_id"] == _TEAM_ID


@pytest.mark.asyncio
async def test_create_session_propagates_value_error_from_resolver() -> None:
    """ValueError from _resolve_personal_team_id must propagate untouched (no wrap/swallow)."""
    store = _store()

    async def fake_resolve(user_id: str) -> str:
        raise ValueError(f"No personal team found for user {user_id}")

    with patch(
        "app.services.ai.chat.conversations_ai_store._resolve_personal_team_id",
        fake_resolve,
    ):
        with pytest.raises(ValueError, match="No personal team found"):
            await store.create_session(
                user_id=_USER_ID,
                agent_slug=_AGENT_SLUG,
                agent_id=_AGENT_ID,
                title="New Chat",
                project_id=None,
                team_id=None,
                context_type=None,
                context_id=None,
            )


@pytest.mark.asyncio
async def test_create_session_inserts_conversation_two_members_and_meta_in_one_txn() -> (
    None
):
    """create_session must run exactly 4 statements inside a single eng.begin() txn:
    conversation INSERT, user-member INSERT, agent-member INSERT, meta INSERT."""
    store = _store()
    eng, conn = _make_engine(
        _MappingResult(_CONV_ROW),
        None,
        None,
        _MappingResult(_META_ROW),
    )

    with patch("app.db.engine.get_engine", return_value=eng):
        result = await store.create_session(
            user_id=_USER_ID,
            agent_slug=_AGENT_SLUG,
            agent_id=_AGENT_ID,
            title="New Chat",
            project_id=None,
            team_id=_TEAM_ID,
            context_type="script",
            context_id="abc",
        )

    assert len(conn.calls) == 4

    conv_sql = conn.calls[0]["sql"]
    assert "INSERT" in conv_sql and "public.conversations" in conv_sql
    assert "'direct_agent'" in conv_sql

    user_member_sql = conn.calls[1]["sql"]
    assert "conversation_members" in user_member_sql
    assert "'user'" in user_member_sql
    assert "'owner'" in user_member_sql
    assert conn.calls[1]["params"]["uid"] == _USER_ID

    agent_member_sql = conn.calls[2]["sql"]
    assert "conversation_members" in agent_member_sql
    assert "'agent'" in agent_member_sql
    assert conn.calls[2]["params"]["agent_id"] == _AGENT_ID

    meta_sql = conn.calls[3]["sql"]
    assert "conversation_ai_meta" in meta_sql
    assert conn.calls[3]["params"]["agent_slug"] == _AGENT_SLUG
    assert conn.calls[3]["params"]["context_type"] == "script"
    assert conn.calls[3]["params"]["context_id"] == "abc"

    # Returned dict carries the legacy shape
    assert _LEGACY_KEYS <= result.keys()
    assert result["status"] == "active"
    assert result["user_id"] == _USER_ID
    assert result["agent_slug"] == _AGENT_SLUG
    assert result["team_id"] == _TEAM_ID


# ── list_sessions ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_sql_has_type_and_archived_filters_and_ordering() -> None:
    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return [_JOINED_ROW]

    store = _store()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await store.list_sessions(
            user_id=_USER_ID, agent_slug=None, project_id=None, limit=50
        )

    sql = captured["sql"]
    assert "c.type = 'direct_agent'" in sql
    assert "c.archived_at IS NULL" in sql
    assert "conversation_members" in sql
    assert "member_type = 'user'" in sql
    assert "cm.user_id = :uid" in sql
    assert "ORDER BY m.updated_at DESC" in sql
    assert "LIMIT :limit" in sql
    assert captured["params"]["uid"] == _USER_ID
    assert captured["params"]["limit"] == 50

    assert len(result) == 1
    assert _LEGACY_KEYS <= result[0].keys()
    assert result[0]["status"] == "active"
    assert result[0]["team_id"] == _TEAM_ID
    assert result[0]["updated_at"] == _JOINED_ROW["updated_at"]
    assert result[0]["user_id"] == _USER_ID
    assert result[0]["agent_id"] == _AGENT_ID


@pytest.mark.asyncio
async def test_list_sessions_applies_agent_slug_and_project_id_filters() -> None:
    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return []

    store = _store()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        await store.list_sessions(
            user_id=_USER_ID, agent_slug=_AGENT_SLUG, project_id=42, limit=10
        )

    sql = captured["sql"]
    assert "m.agent_slug = :agent_slug" in sql
    assert "c.project_id = :project_id" in sql
    assert captured["params"]["agent_slug"] == _AGENT_SLUG
    assert captured["params"]["project_id"] == 42


# ── get_session ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session_returns_none_when_row_missing_or_archived() -> None:
    async def fake_fetch_one(sql: str, params: dict | None = None) -> None:
        # archived rows are filtered out in SQL (archived_at IS NULL), so a
        # missing/archived session simply yields no row.
        assert "c.archived_at IS NULL" in sql
        return None

    store = _store()
    with patch("app.db.engine.fetch_one", fake_fetch_one):
        result = await store.get_session(session_id=_CONV_ID)

    assert result is None


@pytest.mark.asyncio
async def test_get_session_maps_joined_row_to_legacy_shape() -> None:
    async def fake_fetch_one(sql: str, params: dict | None = None) -> dict:
        assert params["cid"] == _CONV_ID
        return _JOINED_ROW

    store = _store()
    with patch("app.db.engine.fetch_one", fake_fetch_one):
        result = await store.get_session(session_id=_CONV_ID)

    assert result is not None
    assert _LEGACY_KEYS <= result.keys()
    assert result["id"] == _CONV_ID
    assert result["user_id"] == _USER_ID
    assert result["agent_id"] == _AGENT_ID
    assert result["status"] == "active"
    assert result["team_id"] == _TEAM_ID
    assert result["total_tokens"] == 12
    assert result["message_count"] == 3


# ── rename_session ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rename_session_updates_title_and_touches_meta_then_returns_row() -> None:
    store = _store()
    eng, conn = _make_engine(None, None)  # UPDATE conversations, UPDATE meta

    async def fake_fetch_one(sql: str, params: dict | None = None) -> dict:
        return {**_JOINED_ROW, "title": "Renamed"}

    with (
        patch("app.db.engine.get_engine", return_value=eng),
        patch("app.db.engine.fetch_one", fake_fetch_one),
    ):
        result = await store.rename_session(session_id=_CONV_ID, title="Renamed")

    assert len(conn.calls) == 2
    assert "UPDATE" in conn.calls[0]["sql"] and "conversations" in conn.calls[0]["sql"]
    assert conn.calls[0]["params"]["title"] == "Renamed"
    assert "conversation_ai_meta" in conn.calls[1]["sql"]
    assert "updated_at = now()" in conn.calls[1]["sql"]

    assert result["title"] == "Renamed"
    assert _LEGACY_KEYS <= result.keys()


# ── soft_delete_session ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_delete_session_sets_archived_at() -> None:
    captured: dict = {}

    async def fake_execute(sql: str, params: dict | None = None) -> int:
        captured["sql"] = sql
        captured["params"] = params
        return 1

    store = _store()
    with patch("app.db.engine.execute", fake_execute):
        await store.soft_delete_session(session_id=_CONV_ID)

    assert "conversations" in captured["sql"]
    assert "archived_at = now()" in captured["sql"]
    assert captured["params"]["cid"] == _CONV_ID


# ── bump_counters ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bump_counters_writes_given_absolutes_not_increment() -> None:
    """bump_counters must write the passed values as-is (SET x = :x), NOT
    SET x = x + :x — the service already computes prior + turn."""
    captured: dict = {}

    async def fake_execute(sql: str, params: dict | None = None) -> int:
        captured["sql"] = sql
        captured["params"] = params
        return 1

    store = _store()
    with patch("app.db.engine.execute", fake_execute):
        await store.bump_counters(session_id=_CONV_ID, add_tokens=555, add_messages=9)

    sql = captured["sql"]
    assert "conversation_ai_meta" in sql
    assert "total_tokens = :total_tokens" in sql
    assert "total_tokens = total_tokens +" not in sql
    assert "message_count = :message_count" in sql
    assert "message_count = message_count +" not in sql
    assert captured["params"]["total_tokens"] == 555
    assert captured["params"]["message_count"] == 9
    assert captured["params"]["cid"] == _CONV_ID


# ── Messages (Task 4) ────────────────────────────────────────────────────────

_MSG_ID = 9001


@pytest.mark.asyncio
async def test_append_user_message_maps_role_and_content() -> None:
    """append_user_message must delegate to ConversationRepository.send_message
    with sender_type='user', type='text', body={'text': content}, and map the
    returned row into the legacy shape (role='user', content passthrough,
    no tokens/agent_id/metadata)."""
    captured: dict = {}

    async def fake_send_message(**kwargs: Any) -> dict:
        captured.update(kwargs)
        return {
            "id": _MSG_ID,
            "conversation_id": _CONV_ID,
            "seq": 1,
            "created_at": "2026-07-03T00:00:00",
        }

    store = _store()
    fake_repo = type("R", (), {"send_message": staticmethod(fake_send_message)})()
    with patch(
        "app.repositories.conversation_repository.get_conversation_repository",
        return_value=fake_repo,
    ):
        result = await store.append_user_message(
            session_id=_CONV_ID, user_id=_USER_ID, content="hello"
        )

    assert captured["conversation_id"] == _CONV_ID
    assert captured["sender_id"] == _USER_ID
    assert captured["sender_type"] == "user"
    assert captured["type"] == "text"
    assert captured["body"] == {"text": "hello"}
    assert captured["parent_id"] is None

    assert result["id"] == _MSG_ID
    assert result["session_id"] == _CONV_ID
    assert result["role"] == "user"
    assert result["content"] == "hello"
    assert result["agent_id"] is None
    assert result["prompt_tokens"] is None
    assert result["completion_tokens"] is None
    assert result["metadata_json"] is None


@pytest.mark.asyncio
async def test_append_assistant_message_metadata_round_trip_via_return_value() -> None:
    """append_assistant_message's own return must carry metadata_json EXACTLY
    as passed by the caller (run_id / tool_calls / awaiting_approval intact),
    with agent_id/tokens/from_agent_id decorated into body['meta'] for send_message."""
    captured: dict = {}
    metadata = {
        "run_id": "r1",
        "tool_calls": [{"a": 1}],
        "awaiting_approval": True,
    }

    async def fake_send_message(**kwargs: Any) -> dict:
        captured.update(kwargs)
        return {
            "id": _MSG_ID,
            "conversation_id": _CONV_ID,
            "seq": 2,
            "created_at": "2026-07-03T00:01:00",
        }

    store = _store()
    fake_repo = type("R", (), {"send_message": staticmethod(fake_send_message)})()
    with patch(
        "app.repositories.conversation_repository.get_conversation_repository",
        return_value=fake_repo,
    ):
        result = await store.append_assistant_message(
            session_id=_CONV_ID,
            agent_id=_AGENT_ID,
            content="the answer",
            prompt_tokens=10,
            completion_tokens=20,
            metadata=metadata,
        )

    # send_message call must decorate body.meta with agent_id/tokens PLUS the
    # caller's metadata keys, and pass from_agent_id explicitly.
    assert captured["sender_id"] is None
    assert captured["sender_type"] == "agent"
    assert captured["from_agent_id"] == _AGENT_ID
    body = captured["body"]
    assert body["text"] == "the answer"
    assert body["meta"]["agent_id"] == _AGENT_ID
    assert body["meta"]["prompt_tokens"] == 10
    assert body["meta"]["completion_tokens"] == 20
    assert body["meta"]["run_id"] == "r1"
    assert body["meta"]["tool_calls"] == [{"a": 1}]
    assert body["meta"]["awaiting_approval"] is True

    # Legacy-shape return: metadata_json is EXACTLY the caller's dict.
    assert result["role"] == "assistant"
    assert result["content"] == "the answer"
    assert result["agent_id"] == _AGENT_ID
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 20
    assert result["metadata_json"] == metadata
    assert result["metadata_json"] is metadata


@pytest.mark.asyncio
async def test_append_assistant_message_handles_none_agent_id() -> None:
    async def fake_send_message(**kwargs: Any) -> dict:
        return {
            "id": _MSG_ID,
            "conversation_id": _CONV_ID,
            "seq": 3,
            "created_at": "2026-07-03T00:02:00",
        }

    store = _store()
    fake_repo = type("R", (), {"send_message": staticmethod(fake_send_message)})()
    with patch(
        "app.repositories.conversation_repository.get_conversation_repository",
        return_value=fake_repo,
    ):
        result = await store.append_assistant_message(
            session_id=_CONV_ID,
            agent_id=None,
            content="hi",
            prompt_tokens=1,
            completion_tokens=1,
            metadata={},
        )

    assert result["agent_id"] is None


@pytest.mark.asyncio
async def test_append_assistant_message_rejects_reserved_metadata_keys() -> None:
    """A caller-supplied metadata key that collides with a decoration field
    (agent_id/prompt_tokens/completion_tokens) must raise ValueError instead
    of silently overwriting the decoration value (see review finding on
    dict-unpack order) — send_message must never even be called.

    The happy-path round trip for non-reserved keys (run_id/tool_calls/
    awaiting_approval) is covered by
    test_append_assistant_message_metadata_round_trip_via_return_value above.
    """
    store = _store()

    async def fake_send_message(**kwargs: Any) -> dict:
        raise AssertionError(
            "send_message must not be called when metadata is rejected"
        )

    with patch(
        "app.repositories.conversation_repository.get_conversation_repository",
        return_value=type("R", (), {"send_message": staticmethod(fake_send_message)})(),
    ):
        with pytest.raises(ValueError, match="reserved"):
            await store.append_assistant_message(
                session_id=_CONV_ID,
                agent_id=_AGENT_ID,
                content="hi",
                prompt_tokens=1,
                completion_tokens=1,
                metadata={"agent_id": "evil"},
            )


@pytest.mark.asyncio
async def test_get_messages_sql_orders_by_seq_asc_and_excludes_deleted() -> None:
    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return []

    store = _store()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        await store.get_messages(session_id=_CONV_ID, limit=50)

    sql = captured["sql"]
    assert "public.messages" in sql
    assert "conversation_id = :cid" in sql
    assert "deleted_at IS NULL" in sql
    assert "ORDER BY seq ASC" in sql
    assert "LIMIT :limit" in sql
    assert captured["params"]["cid"] == _CONV_ID
    assert captured["params"]["limit"] == 50


@pytest.mark.asyncio
async def test_get_messages_maps_role_content_and_strips_meta_decoration() -> None:
    """The decoration keys (agent_id/prompt_tokens/completion_tokens) written
    into body.meta by append_assistant_message must be unpacked into their own
    fields AND stripped back out of metadata_json — reconstructing exactly
    the metadata dict the caller originally passed."""
    rows = [
        {
            "id": 1,
            "conversation_id": _CONV_ID,
            "seq": 1,
            "sender_type": "user",
            "sender_id": _USER_ID,
            "from_agent_id": None,
            "type": "text",
            "body": {"text": "hi there"},
            "created_at": "2026-07-03T00:00:00",
        },
        {
            "id": 2,
            "conversation_id": _CONV_ID,
            "seq": 2,
            "sender_type": "agent",
            "sender_id": None,
            "from_agent_id": _AGENT_ID,
            "type": "text",
            "body": {
                "text": "the answer",
                "meta": {
                    "agent_id": _AGENT_ID,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "run_id": "r1",
                    "tool_calls": [{"a": 1}],
                    "awaiting_approval": True,
                },
            },
            "created_at": "2026-07-03T00:01:00",
        },
    ]

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        return rows

    store = _store()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await store.get_messages(session_id=_CONV_ID)

    assert len(result) == 2

    user_msg = result[0]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "hi there"
    assert user_msg["agent_id"] is None
    assert user_msg["prompt_tokens"] is None
    assert user_msg["completion_tokens"] is None
    assert user_msg["metadata_json"] is None
    assert user_msg["id"] == 1
    assert user_msg["session_id"] == _CONV_ID

    asst_msg = result[1]
    assert asst_msg["role"] == "assistant"
    assert asst_msg["content"] == "the answer"
    assert asst_msg["agent_id"] == _AGENT_ID
    assert asst_msg["prompt_tokens"] == 10
    assert asst_msg["completion_tokens"] == 20
    # metadata_json must be EXACTLY the original caller metadata — no
    # agent_id/prompt_tokens/completion_tokens leaking through.
    assert asst_msg["metadata_json"] == {
        "run_id": "r1",
        "tool_calls": [{"a": 1}],
        "awaiting_approval": True,
    }
    assert "agent_id" not in asst_msg["metadata_json"]
    assert "prompt_tokens" not in asst_msg["metadata_json"]
    assert "completion_tokens" not in asst_msg["metadata_json"]


@pytest.mark.asyncio
async def test_get_messages_defensive_json_loads_when_body_is_str() -> None:
    """Defends against a driver variance where JSONB body arrives as raw text
    instead of an already-decoded dict."""
    rows = [
        {
            "id": 1,
            "conversation_id": _CONV_ID,
            "seq": 1,
            "sender_type": "user",
            "sender_id": _USER_ID,
            "from_agent_id": None,
            "type": "text",
            "body": '{"text": "raw json text"}',
            "created_at": "2026-07-03T00:00:00",
        }
    ]

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        return rows

    store = _store()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await store.get_messages(session_id=_CONV_ID)

    assert result[0]["content"] == "raw json text"


# ── store_kind ───────────────────────────────────────────────────────────────


def test_store_kind_is_conversations() -> None:
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    assert ConversationsAiStore.store_kind == "conversations"


@pytest.mark.asyncio
async def test_create_session_return_dict_carries_store_kind_key() -> None:
    """Task 6: create_session's returned row must carry a 'store_kind' key
    (not just the class attribute) so the service can dispatch RunRecorder's
    session_id vs conversation_id choice off the session row alone."""
    store = _store()
    eng, conn = _make_engine(
        _MappingResult(_CONV_ROW),
        None,
        None,
        _MappingResult(_META_ROW),
    )

    with patch("app.db.engine.get_engine", return_value=eng):
        result = await store.create_session(
            user_id=_USER_ID,
            agent_slug=_AGENT_SLUG,
            agent_id=_AGENT_ID,
            title="New Chat",
            project_id=None,
            team_id=_TEAM_ID,
            context_type=None,
            context_id=None,
        )

    assert result["store_kind"] == "conversations"


@pytest.mark.asyncio
async def test_get_session_return_dict_carries_store_kind_key() -> None:
    """Task 6: get_session's returned row must carry a 'store_kind' key."""

    async def fake_fetch_one(sql: str, params: dict | None = None) -> dict:
        return _JOINED_ROW

    store = _store()
    with patch("app.db.engine.fetch_one", fake_fetch_one):
        result = await store.get_session(session_id=_CONV_ID)

    assert result["store_kind"] == "conversations"


# ── Integration test (skippable without INTEGRATION_DATABASE_URL) ─────────────

_INTEGRATION_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_SMOKE_CREATOR_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"


@pytest.fixture
async def smoke_ctx():
    """Real-DB fixture: resolve/create a personal team + a real ai_agents row
    for _SMOKE_CREATOR_ID, patch db_engine at the integration DSN, and clean
    up any conversations the test created (list appended by the test) plus
    any team this fixture had to create.

    Mirrors ``conv_for_smoke`` in test_conversation_repository.py.
    """
    if not _INTEGRATION_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    import time

    import asyncpg

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _INTEGRATION_DSN):
        conn = await asyncpg.connect(_INTEGRATION_DSN)
        created_team_id = None
        conv_ids: list[int] = []
        try:
            user_row = await conn.fetchrow(
                "SELECT id FROM auth.users WHERE id = $1", _SMOKE_CREATOR_ID
            )
            if not user_row:
                pytest.skip(
                    f"creator {_SMOKE_CREATOR_ID!r} not found in auth.users — "
                    "cannot run integration smoke"
                )

            agent_row = await conn.fetchrow(
                "SELECT id, slug FROM public.ai_agents WHERE slug IS NOT NULL LIMIT 1"
            )
            if not agent_row:
                pytest.skip("no ai_agents rows found — cannot run integration smoke")

            team_row = await conn.fetchrow(
                "SELECT id FROM public.teams WHERE owner_id = $1 AND kind = 'personal'",
                _SMOKE_CREATOR_ID,
            )
            if team_row:
                team_id = team_row["id"]
            else:
                team_id = await conn.fetchval(
                    """
                    INSERT INTO public.teams (name, owner_id, invite_code, kind)
                    VALUES ($1, $2, $3, 'personal')
                    RETURNING id
                    """,
                    "__smoke_personal_team__",
                    _SMOKE_CREATOR_ID,
                    f"SMOKE{int(time.time())}",
                )
                created_team_id = team_id

            import app.services.ai.chat.conversations_ai_store as _store_mod

            yield {
                "store": _store_mod.ConversationsAiStore(),
                "agent_id": str(agent_row["id"]),
                "agent_slug": agent_row["slug"],
                "team_id": team_id,
                "conv_ids": conv_ids,
            }
        finally:
            for cid in conv_ids:
                await conn.execute(
                    "DELETE FROM public.conversations WHERE id = $1", cid
                )
            if created_team_id is not None:
                await conn.execute(
                    "DELETE FROM public.teams WHERE id = $1", created_team_id
                )
            await conn.close()

    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_smoke_create_list_rename_get_delete(smoke_ctx: dict) -> None:
    """Real-DB smoke: create → appears in list → rename → get shows title +
    user_id → soft-delete → absent from list, get → None."""
    store = smoke_ctx["store"]

    created = await store.create_session(
        user_id=_SMOKE_CREATOR_ID,
        agent_slug=smoke_ctx["agent_slug"],
        agent_id=smoke_ctx["agent_id"],
        title="__smoke_ai_store_test__",
        project_id=None,
        team_id=smoke_ctx["team_id"],
        context_type="script",
        context_id="smoke-1",
    )
    session_id = created["id"]
    smoke_ctx["conv_ids"].append(session_id)

    assert created["status"] == "active"
    assert created["user_id"] == _SMOKE_CREATOR_ID
    assert created["agent_slug"] == smoke_ctx["agent_slug"]
    assert created["team_id"] == smoke_ctx["team_id"]

    listed = await store.list_sessions(
        user_id=_SMOKE_CREATOR_ID,
        agent_slug=smoke_ctx["agent_slug"],
        project_id=None,
        limit=50,
    )
    assert any(s["id"] == session_id for s in listed)

    renamed = await store.rename_session(session_id=session_id, title="Renamed Smoke")
    assert renamed["title"] == "Renamed Smoke"

    fetched = await store.get_session(session_id=session_id)
    assert fetched is not None
    assert fetched["title"] == "Renamed Smoke"
    assert fetched["user_id"] == _SMOKE_CREATOR_ID

    await store.soft_delete_session(session_id=session_id)

    listed_after = await store.list_sessions(
        user_id=_SMOKE_CREATOR_ID,
        agent_slug=smoke_ctx["agent_slug"],
        project_id=None,
        limit=50,
    )
    assert all(s["id"] != session_id for s in listed_after)

    gone = await store.get_session(session_id=session_id)
    assert gone is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_smoke_append_and_get_messages_round_trip(smoke_ctx: dict) -> None:
    """Real-DB smoke: create session -> append user + decorated assistant
    message -> get_messages returns both chronological with decoration
    intact byte-for-byte -> each row constructs a valid MessageOut (proving
    serializer compatibility end-to-end, incl. the widened `id: str`)."""
    from app.schemas.ai_library_chat import MessageOut

    store = smoke_ctx["store"]

    created = await store.create_session(
        user_id=_SMOKE_CREATOR_ID,
        agent_slug=smoke_ctx["agent_slug"],
        agent_id=smoke_ctx["agent_id"],
        title="__smoke_ai_store_messages_test__",
        project_id=None,
        team_id=smoke_ctx["team_id"],
        context_type="script",
        context_id="smoke-msg-1",
    )
    session_id = created["id"]
    smoke_ctx["conv_ids"].append(session_id)

    user_msg = await store.append_user_message(
        session_id=session_id, user_id=_SMOKE_CREATOR_ID, content="hello agent"
    )
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "hello agent"

    metadata = {
        "run_id": "smoke-run-1",
        "tool_calls": [{"name": "Skill", "iteration": 1, "args": {}, "result": {}}],
        "awaiting_approval": {"approval_id": "abc", "hook": "h", "reason": "r"},
    }
    asst_msg = await store.append_assistant_message(
        session_id=session_id,
        agent_id=smoke_ctx["agent_id"],
        content="hello human",
        prompt_tokens=7,
        completion_tokens=13,
        metadata=metadata,
    )
    assert asst_msg["metadata_json"] == metadata

    messages = await store.get_messages(session_id=session_id)
    assert len(messages) == 2
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hello agent"
    assert messages[1]["content"] == "hello human"
    assert messages[1]["agent_id"] == str(smoke_ctx["agent_id"])
    assert messages[1]["prompt_tokens"] == 7
    assert messages[1]["completion_tokens"] == 13
    # Decoration must survive the DB round trip byte-for-byte.
    assert messages[1]["metadata_json"] == metadata

    # Serializer-compatibility proof: every returned row constructs a valid
    # MessageOut, including the widened `id: str` (native BIGINT from this
    # store, not a UUID).
    for row in messages:
        mo = MessageOut(**row)
        assert mo.id == str(row["id"])
        assert mo.session_id == str(session_id)
