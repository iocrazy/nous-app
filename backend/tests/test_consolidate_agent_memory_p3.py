"""Conversations Phase 3 (Task 2, Wave 0): /dream consolidation reads the
canonical conversations/messages store instead of legacy ai_sessions/
ai_messages.

ALL 1:1 direct_agent traffic lands in conversations/messages now — the
legacy ai_sessions/ai_messages store and its compatibility layer have been
retired (Task 6). Phase 3 Wave 2 will DROP the legacy tables entirely, so
this workflow is rewritten conversations-ONLY (no dual-read fallback).

ORM (Phase B4): the three raw SQL constants (_ACTIVE_PAIRS_SQL /
_RECENT_MESSAGES_SQL / _PAIR_CONTEXTS_SQL) became SQLAlchemy Core statement
builders (_active_pairs_stmt / _recent_messages_stmt / _pair_contexts_stmt)
through app.db.session.read_scope(). Covers:
  (a) the three statement builders target conversations/messages, never
      ai_sessions/ai_messages (checked via the compiled SQL text)
  (b) role/content mapping (sender_type='agent'->'assistant', body->>'text')
  (c) grouping/threshold semantics (MIN_NEW_MESSAGES filter, NULL-safe
      team_id/project_id context matching) preserved end-to-end through
      _consolidate_context / enumerate_active_pairs_step

Harness mirrors tests/test_orm_b3_task1_compile_coverage.py: monkeypatch
app.db.session.read_scope with a recording session and inspect the compiled
statement.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows.consolidate_agent_memory import (
    MIN_NEW_MESSAGES,
    _active_pairs_stmt,
    _consolidate_context,
    _pair_contexts_stmt,
    _recent_messages_stmt,
    enumerate_active_pairs_step,
)

_LEGACY_TABLES = ("ai_sessions", "ai_messages")


def _compile(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# ---------------------------------------------------------------------------
# (a) statement builders target conversations/messages, never legacy tables
# ---------------------------------------------------------------------------


def test_active_pairs_stmt_targets_conversations_not_legacy():
    sql = _compile(_active_pairs_stmt())
    for legacy in _LEGACY_TABLES:
        assert legacy not in sql, f"_active_pairs_stmt must not reference {legacy}"
    assert "public.conversations" in sql
    assert "public.conversation_ai_meta" in sql
    assert "public.conversation_members" in sql
    assert "public.messages" in sql
    assert "member_type =" in sql
    assert "conversations.type =" in sql
    assert "conversations.archived_at" in sql


def test_recent_messages_stmt_targets_conversations_not_legacy():
    sql = _compile(_recent_messages_stmt("u1", "a1", None, None))
    for legacy in _LEGACY_TABLES:
        assert legacy not in sql, f"_recent_messages_stmt must not reference {legacy}"
    assert "public.messages" in sql
    assert "public.conversations" in sql
    assert "public.conversation_ai_meta" in sql
    assert "public.conversation_members" in sql


def test_pair_contexts_stmt_targets_conversations_not_legacy():
    sql = _compile(_pair_contexts_stmt("u1", "a1"))
    for legacy in _LEGACY_TABLES:
        assert legacy not in sql, f"_pair_contexts_stmt must not reference {legacy}"
    assert "public.conversations" in sql
    assert "public.conversation_ai_meta" in sql
    assert "public.conversation_members" in sql


# ---------------------------------------------------------------------------
# (b) role/content mapping
# ---------------------------------------------------------------------------


def test_recent_messages_stmt_maps_agent_sender_to_assistant_role():
    stmt = _recent_messages_stmt("u1", "a1", None, None)
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "sender_type =" in sql
    assert compiled.params["sender_type_1"] == "agent"
    assert compiled.params["param_1"] == "assistant"
    assert "AS role" in sql


def test_recent_messages_stmt_reads_body_text_as_content():
    stmt = _recent_messages_stmt("u1", "a1", None, None)
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "body ->>" in sql
    assert compiled.params["body_1"] == "text"
    assert "AS content" in sql
    assert "deleted_at IS NULL" in sql


def test_active_pairs_stmt_filters_deleted_messages():
    assert "deleted_at IS NULL" in _compile(_active_pairs_stmt())


# ---------------------------------------------------------------------------
# (c) grouping / threshold semantics preserved
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.calls: list[Any] = []
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._results.pop(0) if self._results else self._default


def _patch_read_scope(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    session = _RecordingSession(results)

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)
    return session


@pytest.mark.asyncio
async def test_enumerate_active_pairs_still_filters_by_min_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MIN_NEW_MESSAGES python-side filter is unaffected by the ORM
    rewrite — it still operates on the msg_count column the query computes
    via COUNT(msg.id)."""
    rows = [
        {
            "user_id": "u1",
            "agent_id": "a1",
            "team_id": None,
            "project_id": None,
            "msg_count": MIN_NEW_MESSAGES,
        },
        {
            "user_id": "u2",
            "agent_id": "a2",
            "team_id": 10,
            "project_id": None,
            "msg_count": MIN_NEW_MESSAGES - 1,
        },
    ]

    session = _patch_read_scope(monkeypatch, [_FakeResult(rows=rows)])

    pairs = await enumerate_active_pairs_step()

    assert len(session.calls) == 1
    assert len(pairs) == 1
    assert pairs[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_consolidate_context_recent_messages_query_is_new_stmt_with_null_safe_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_context's first read_scope call must be the
    _recent_messages_stmt, carrying user_id/agent_id/team_id/project_id
    (NULL-safe personal-scope context) exactly like the legacy call did."""
    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    session = _patch_read_scope(
        monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])]
    )

    async def fake_existing_fps(**kwargs) -> set:
        return set()

    async def fake_write(**kwargs) -> bool:
        return True

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return "[]"  # no drafts — keeps this test focused on the read path

    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row",
        fake_write,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    assert result["written"] == 0  # empty consolidator output
    assert len(session.calls) == 2
    first_sql = _compile(session.calls[0])
    assert "public.messages" in first_sql
    assert "public.conversation_ai_meta" in first_sql


@pytest.mark.asyncio
async def test_consolidate_context_recent_messages_query_carries_team_project_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team+project context passes team_id/project_id through unchanged
    to the new statement — same NULL-safe param contract as before the ORM
    rewrite."""
    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]
    session = _patch_read_scope(
        monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])]
    )

    async def fake_existing_fps(**kwargs) -> set:
        return set()

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return "[]"

    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )

    await _consolidate_context("u1", "a1", team_id=10, project_id=55)

    first_compiled = session.calls[0].compile(dialect=postgresql.dialect())
    params = dict(first_compiled.params)
    # team_id=10 is carried inside the personal-team CASE expression's
    # IS NOT DISTINCT FROM comparison — since team_id != None, SQLAlchemy
    # binds it (a NULL comparand renders as a literal instead).
    assert 10 in params.values()
    assert 55 in params.values()


@pytest.mark.asyncio
async def test_consolidate_context_below_threshold_still_skips_with_new_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threshold semantics (MIN_NEW_MESSAGES) are unchanged by the ORM
    rewrite: too few rows from the recent-messages read still yields an
    early no-write return."""
    few_messages = [{"role": "user", "content": "hi"}] * (MIN_NEW_MESSAGES - 1)
    write_called = [False]

    _patch_read_scope(monkeypatch, [_FakeResult(rows=few_messages)])

    async def fake_write(**kwargs) -> bool:
        write_called[0] = True
        return True

    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row",
        fake_write,
    )

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    assert result["written"] == 0
    assert not write_called[0]
