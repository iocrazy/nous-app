"""Unit + integration tests for ConversationRepository (Task 4).

Unit tests (default, no DB): capture SQL via fake engine / mocked module-level
helpers, following the pattern in test_chat_edit_delete.py.

Integration tests (skippable): require INTEGRATION_DATABASE_URL env var.
They run against the real asyncpg / Postgres to prove the name/title bridge,
CAST(:before AS bigint) keyset, and dual-FK member_type filters work in production.

Minimum assertions (brief §Step 1):
  - send_message allocates seq via UPDATE conversations + inserts with
    type / parent_id / from_agent_id
  - is_member filters member_type='user'
  - add_agent_member inserts member_type='agent'
  - list_conversation_agent_ids filters member_type='agent'
  - add_attachments inserts one row per id with incrementing ord
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fake engine helpers ───────────────────────────────────────────────────────


class _ScalarResult:
    """Mimics a SQLAlchemy result that supports .scalar_one() / .scalar()."""

    def __init__(self, v: Any) -> None:
        self._v = v

    def scalar_one(self) -> Any:  # noqa: D102
        return self._v

    def scalar(self) -> Any:  # noqa: D102
        return self._v


class _MappingResult:
    """Mimics a SQLAlchemy result that supports .mappings().one() / .first() / .all()."""

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
    """Return (engine, conn) pair loaded with the given result sequence."""
    conn = _FakeConn(*results)
    return _FakeEngine(conn), conn


# ── Constants reused across tests ─────────────────────────────────────────────

_CONV_ID = 285274231427073
_SENDER_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"
_SEQ = 1
_MSG_ROW = {
    "id": 9001,
    "conversation_id": _CONV_ID,
    "seq": _SEQ,
    "sender_id": _SENDER_ID,
    "sender_type": "user",
    "type": "text",
    "body": {"text": "hi"},
    "parent_id": None,
    "from_agent_id": None,
    "edited_at": None,
    "deleted_at": None,
    "created_at": "2026-06-30T00:00:00",
}

# ── send_message ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_seq_via_update_conversations() -> None:
    """send_message must UPDATE conversations to allocate seq before INSERTing."""
    from app.repositories.conversation_repository import ConversationRepository

    eng, conn = _make_engine(
        _ScalarResult(_SEQ),  # UPDATE conversations RETURNING last_seq
        _MappingResult(_MSG_ROW),  # INSERT INTO messages RETURNING *
        MagicMock(rowcount=1),  # UPDATE conversation_members (read cursor)
    )
    repo = ConversationRepository()
    with patch("app.db.engine.get_engine", return_value=eng):
        result = await repo.send_message(
            conversation_id=_CONV_ID,
            sender_id=_SENDER_ID,
            sender_type="user",
            type="text",
            body={"text": "hi"},
            parent_id=None,
        )

    # First call: seq allocation via UPDATE conversations
    first_sql = conn.calls[0]["sql"]
    assert "UPDATE" in first_sql
    assert "conversations" in first_sql
    assert "last_seq" in first_sql
    assert "RETURNING" in first_sql

    # Second call: INSERT INTO messages with renamed column names
    second_sql = conn.calls[1]["sql"]
    assert "INSERT" in second_sql
    assert "messages" in second_sql
    # New column names must appear
    assert "type" in second_sql
    assert "parent_id" in second_sql
    assert "from_agent_id" in second_sql
    # Old column names must NOT appear
    assert "content_type" not in second_sql
    assert "reply_to_id" not in second_sql
    assert "from_bot_agent_id" not in second_sql

    # Returned dict must have new names
    assert result["type"] == "text"
    assert "parent_id" in result
    assert "from_agent_id" in result


@pytest.mark.asyncio
async def test_send_message_advances_sender_read_cursor_for_user() -> None:
    """send_message must advance the sender's read cursor when sender_type='user'."""
    from app.repositories.conversation_repository import ConversationRepository

    eng, conn = _make_engine(
        _ScalarResult(_SEQ),
        _MappingResult(_MSG_ROW),
        MagicMock(rowcount=1),
    )
    repo = ConversationRepository()
    with patch("app.db.engine.get_engine", return_value=eng):
        await repo.send_message(
            conversation_id=_CONV_ID,
            sender_id=_SENDER_ID,
            sender_type="user",
            type="text",
            body={"text": "hi"},
            parent_id=None,
        )

    assert len(conn.calls) == 3
    third_sql = conn.calls[2]["sql"]
    assert "conversation_members" in third_sql
    assert "last_read_seq" in third_sql
    assert "member_type" in third_sql
    assert "'user'" in third_sql


@pytest.mark.asyncio
async def test_send_message_no_read_cursor_advance_for_agent() -> None:
    """send_message must NOT advance the read cursor when sender_type='agent'."""
    from app.repositories.conversation_repository import ConversationRepository

    _agent_row = {**_MSG_ROW, "sender_type": "agent", "sender_id": None}
    eng, conn = _make_engine(
        _ScalarResult(_SEQ),
        _MappingResult(_agent_row),
    )
    repo = ConversationRepository()
    with patch("app.db.engine.get_engine", return_value=eng):
        await repo.send_message(
            conversation_id=_CONV_ID,
            sender_id=None,
            sender_type="agent",
            type="text",
            body={"text": "agent says hi"},
            parent_id=None,
            from_agent_id="agent-uuid",
        )

    # Exactly 2 execute calls (seq UPDATE + INSERT); no read cursor UPDATE
    assert len(conn.calls) == 2


# ── is_member ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_member_filters_member_type_user() -> None:
    """is_member WHERE clause must include member_type='user' alongside user_id."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_val(sql: str, params: dict | None = None) -> bool:
        captured["sql"] = sql
        captured["params"] = params
        return True

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_val", fake_fetch_val):
        result = await repo.is_member(conversation_id=_CONV_ID, user_id=_SENDER_ID)

    assert result is True
    sql = captured["sql"]
    # Must reference conversation_members (not channel_members)
    assert "conversation_members" in sql
    # Must gate on member_type='user'
    assert "member_type" in sql
    assert "'user'" in sql
    assert "user_id" in sql


# ── get_my_conversations (final review — direct_agent exclusion) ──────────────


@pytest.mark.asyncio
async def test_get_my_conversations_excludes_direct_agent() -> None:
    """get_my_conversations must exclude type='direct_agent' rows so 1:1 AI
    session threads (Phase 2) don't leak into the team-chat sidebar list."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return []

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await repo.get_my_conversations(_SENDER_ID)

    assert result == []
    sql = captured["sql"]
    assert "c.archived_at IS NULL" in sql
    assert "c.type <> 'direct_agent'" in sql


# ── add_agent_member ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_agent_member_inserts_member_type_agent() -> None:
    """add_agent_member must INSERT into conversation_members with member_type='agent'."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_execute(sql: str, params: dict | None = None) -> int:
        captured["sql"] = sql
        captured["params"] = params
        return 1

    repo = ConversationRepository()
    with patch("app.db.engine.execute", fake_execute):
        await repo.add_agent_member(
            conversation_id=_CONV_ID,
            agent_id="agent-uuid-001",
            added_by=_SENDER_ID,
        )

    sql = captured["sql"]
    # Must target conversation_members (not old agent_channels table)
    assert "conversation_members" in sql
    assert "agent_channels" not in sql
    # Must embed member_type='agent' literal
    assert "member_type" in sql
    assert "'agent'" in sql
    # Must include agent_id column
    assert "agent_id" in sql
    # Must be idempotent
    assert "ON CONFLICT" in sql


# ── list_conversation_agent_ids ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_conversation_agent_ids_filters_member_type_agent() -> None:
    """list_conversation_agent_ids must SELECT from conversation_members WHERE member_type='agent'."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return []

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await repo.list_conversation_agent_ids(conversation_id=_CONV_ID)

    assert result == []
    sql = captured["sql"]
    assert "conversation_members" in sql
    assert "member_type" in sql
    assert "'agent'" in sql
    assert "agent_id" in sql
    # Must NOT confuse with old agent_channels table
    assert "agent_channels" not in sql


# ── add_attachments ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_attachments_inserts_one_row_per_id_with_ord() -> None:
    """add_attachments must INSERT one row per generated_media_id with ord=0,1,2,..."""
    from app.repositories.conversation_repository import ConversationRepository

    _MSG_ID = 5001
    _GIDS = [1001, 1002, 1003]

    eng, conn = _make_engine(
        MagicMock(rowcount=1),
        MagicMock(rowcount=1),
        MagicMock(rowcount=1),
    )
    repo = ConversationRepository()
    with patch("app.db.engine.get_engine", return_value=eng):
        await repo.add_attachments(message_id=_MSG_ID, generated_media_ids=_GIDS)

    assert len(conn.calls) == 3, f"Expected 3 execute calls, got {len(conn.calls)}"
    for i, call in enumerate(conn.calls):
        sql = call["sql"]
        params = call["params"]
        assert (
            "message_attachments" in sql
        ), f"call {i}: expected message_attachments in SQL"
        assert "ON CONFLICT" in sql, f"call {i}: expected ON CONFLICT DO NOTHING"
        assert params["ord"] == i, f"call {i}: expected ord={i}, got {params['ord']}"
        assert (
            params["gid"] == _GIDS[i]
        ), f"call {i}: expected gid={_GIDS[i]}, got {params['gid']}"


@pytest.mark.asyncio
async def test_add_attachments_empty_list_is_noop() -> None:
    """add_attachments with an empty list must not touch the DB."""
    from app.repositories.conversation_repository import ConversationRepository

    eng, conn = _make_engine()
    repo = ConversationRepository()
    with patch("app.db.engine.get_engine", return_value=eng):
        await repo.add_attachments(message_id=5001, generated_media_ids=[])

    assert len(conn.calls) == 0


# ── messages_in_range ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_messages_in_range_bounds_and_excludes_deleted() -> None:
    """messages_in_range must bound seq on both ends, exclude soft-deleted rows,
    and return ascending (oldest-first) order — the compaction feed's contract."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return []

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await repo.messages_in_range(conversation_id=1, from_seq=5, to_seq=40)

    assert result == []
    sql = captured["sql"]
    assert "seq >= :from_seq" in sql and "seq <= :to_seq" in sql
    assert "deleted_at IS NULL" in sql
    assert "ORDER BY seq ASC" in sql
    params = captured["params"]
    assert params["from_seq"] == 5
    assert params["to_seq"] == 40
    assert params["cid"] == 1


# ── list_messages (joined-gate carryover, Phase-1 final review) ────────────────


@pytest.mark.asyncio
async def test_list_messages_no_joined_gate_when_for_user_id_omitted() -> None:
    """The historical (Phase 1) shape is unchanged when for_user_id is not
    passed: no join on conversations, no history_mode/joined_at predicate."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return [_MSG_ROW]

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await repo.list_messages(
            conversation_id=_CONV_ID, before_seq=None, limit=10
        )

    assert result == [_MSG_ROW]
    sql = captured["sql"]
    assert "history_mode" not in sql
    assert "joined_at" not in sql
    assert "JOIN public.conversations" not in sql
    assert "ORDER BY seq DESC" in sql
    assert "for_uid" not in captured["params"]


@pytest.mark.asyncio
async def test_list_messages_applies_joined_gate_when_for_user_id_given() -> None:
    """for_user_id given must add the mig-328 messages_select RLS-mirroring
    predicate: shared mode passes through unconditionally, joined mode is cut
    off at the caller's own conversation_members.joined_at."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_all(sql: str, params: dict | None = None) -> list:
        captured["sql"] = sql
        captured["params"] = params
        return [_MSG_ROW]

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await repo.list_messages(
            conversation_id=_CONV_ID,
            before_seq=None,
            limit=10,
            for_user_id=_SENDER_ID,
        )

    assert result == [_MSG_ROW]
    sql = captured["sql"]
    assert "JOIN public.conversations c" in sql
    assert "c.history_mode = 'shared'" in sql
    assert "conversation_members cm" in sql
    assert "cm.member_type = 'user'" in sql
    assert "cm.user_id = :for_uid" in sql
    assert "m.created_at >=" in sql
    assert captured["params"]["for_uid"] == _SENDER_ID
    assert captured["params"]["cid"] == _CONV_ID


# ── name / title bridge ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_conversation_inserts_title_column_not_name() -> None:
    """INSERT must use column `title`, bind param :name; RETURNING must alias title AS name."""
    from app.repositories.conversation_repository import ConversationRepository

    _CONV_ROW = {
        "id": _CONV_ID,
        "scope_id": 285274231427073,
        "type": "group",
        "history_mode": "shared",
        "name": "My Channel",
        "topic": None,
        "last_seq": 0,
        "created_at": "2026-06-30T00:00:00",
    }

    eng, conn = _make_engine(
        _MappingResult(_CONV_ROW),  # INSERT INTO conversations RETURNING
        MagicMock(rowcount=1),  # INSERT INTO conversation_members (creator/owner)
    )
    repo = ConversationRepository()
    with patch("app.db.engine.get_engine", return_value=eng):
        result = await repo.create_conversation(
            creator_id=_SENDER_ID,
            scope_id=285274231427073,
            type="group",
            name="My Channel",
            history_mode="shared",
            member_ids=[],
        )

    insert_sql = conn.calls[0]["sql"]
    # DB column must be `title`, NOT `name`
    assert "title" in insert_sql
    # But the Python dict key returned must be `name`
    assert result["name"] == "My Channel"
    # Returned dict must NOT have a 'title' key at the top level
    # (it's aliased to `name` in the RETURNING clause)
    assert "title" not in result


# ── is_agent_member ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_agent_member_filters_member_type_agent() -> None:
    """is_agent_member must gate on member_type='agent' in conversation_members."""
    from app.repositories.conversation_repository import ConversationRepository

    captured: dict = {}

    async def fake_fetch_val(sql: str, params: dict | None = None) -> bool:
        captured["sql"] = sql
        return True

    repo = ConversationRepository()
    with patch("app.db.engine.fetch_val", fake_fetch_val):
        result = await repo.is_agent_member(conversation_id=_CONV_ID, agent_id="ag-001")

    assert result is True
    sql = captured["sql"]
    assert "conversation_members" in sql
    assert "member_type" in sql
    assert "'agent'" in sql


# ── get_conversation_repository singleton ─────────────────────────────────────


def test_get_conversation_repository_returns_singleton() -> None:
    """get_conversation_repository() must return the same instance on repeated calls."""
    import importlib

    import app.repositories.conversation_repository as mod
    from app.repositories.conversation_repository import (
        ConversationRepository,
        get_conversation_repository,
    )

    # Reset the module-level singleton so test is idempotent
    mod._repo = None
    r1 = get_conversation_repository()
    r2 = get_conversation_repository()
    assert r1 is r2
    assert isinstance(r1, ConversationRepository)
    # Clean up
    mod._repo = None


# ── Integration tests (skippable without INTEGRATION_DATABASE_URL) ─────────────

_INTEGRATION_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_SMOKE_SCOPE_ID = 285274231427073
_SMOKE_CREATOR_ID = "ca5e636f-6e60-414c-be54-110acf8c45c7"


@pytest.fixture
async def conv_for_smoke():
    """Create a real conversation against the integration DB, clean up on exit.

    Skips if INTEGRATION_DATABASE_URL is not set.
    Patches db_engine to point at the integration DSN so the repository works
    against the same database.
    """
    if not _INTEGRATION_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")

    import asyncpg

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", _INTEGRATION_DSN):
        conn = await asyncpg.connect(_INTEGRATION_DSN)
        conv_id = None
        try:
            # Verify the creator exists in auth.users
            row = await conn.fetchrow(
                "SELECT id FROM auth.users WHERE id = $1",
                _SMOKE_CREATOR_ID,
            )
            if not row:
                pytest.skip(
                    f"creator {_SMOKE_CREATOR_ID!r} not found in auth.users — "
                    "cannot run integration smoke"
                )

            # Reset singleton so it picks up the patched engine DSN
            import app.repositories.conversation_repository as _conv_mod
            from app.repositories.conversation_repository import (
                get_conversation_repository,
            )

            _conv_mod._repo = None
            repo = get_conversation_repository()

            c = await repo.create_conversation(
                creator_id=_SMOKE_CREATOR_ID,
                scope_id=_SMOKE_SCOPE_ID,
                type="group",
                name="__smoke_conv_repo_test__",
                history_mode="shared",
                member_ids=[],
            )
            conv_id = c["id"]
            yield conv_id

        finally:
            if conv_id is not None:
                await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)
            await conn.close()
            _conv_mod._repo = None  # type: ignore[union-attr]

    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_smoke_send_3_messages_seq_1_2_3(conv_for_smoke: int) -> None:
    """Real-DB smoke: 3 messages → seq=1,2,3; list_messages returns desc; keyset works.

    Proves:
      - name/title bridge (INSERT uses `title` column, dict key stays `name`)
      - CAST(:before AS bigint) keyset pagination (no AmbiguousParameterError on NULL)
      - dual-FK member_type filters work against real asyncpg
    """
    import app.repositories.conversation_repository as _conv_mod

    repo = _conv_mod.get_conversation_repository()
    conv_id = conv_for_smoke

    msgs = []
    for i in range(3):
        m = await repo.send_message(
            conversation_id=conv_id,
            sender_id=_SMOKE_CREATOR_ID,
            sender_type="user",
            type="text",
            body={"text": f"smoke message {i + 1}"},
            parent_id=None,
        )
        msgs.append(m)

    seqs = [m["seq"] for m in msgs]
    assert seqs == [1, 2, 3], f"Expected seq [1,2,3], got {seqs}"

    # list_messages with before_seq=None returns all 3 in DESC order
    page1 = await repo.list_messages(conversation_id=conv_id, before_seq=None, limit=10)
    assert [m["seq"] for m in page1] == [
        3,
        2,
        1,
    ], f"Expected desc order [3,2,1], got {[m['seq'] for m in page1]}"

    # Keyset pagination: before_seq=3 → [2, 1]
    page2 = await repo.list_messages(conversation_id=conv_id, before_seq=3, limit=10)
    assert [m["seq"] for m in page2] == [
        2,
        1,
    ], f"Expected [2,1], got {[m['seq'] for m in page2]}"

    # Verify is_member works
    is_mem = await repo.is_member(conversation_id=conv_id, user_id=_SMOKE_CREATOR_ID)
    assert is_mem is True
