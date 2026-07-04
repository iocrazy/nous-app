"""Conversations Phase 3 (Task 2, Wave 0): /dream consolidation reads the
canonical conversations/messages store instead of legacy ai_sessions/
ai_messages.

ALL 1:1 direct_agent traffic lands in conversations/messages now — the
legacy ai_sessions/ai_messages store and its compatibility layer have been
retired (Task 6). Phase 3 Wave 2 will DROP the legacy tables entirely, so
this workflow is rewritten conversations-ONLY (no dual-read fallback).

Covers:
  (a) the three SQL constants target conversations/messages, never
      ai_sessions/ai_messages
  (b) role/content mapping (sender_type='agent'->'assistant', body->>'text')
  (c) grouping/threshold semantics (MIN_NEW_MESSAGES filter, NULL-safe
      team_id/project_id context matching) preserved end-to-end through
      _consolidate_context / enumerate_active_pairs_step

Harness mirrors tests/memory/test_consolidate_workflow.py and
tests/test_write_memory_load_recent_messages.py: monkeypatch
app.db.engine.fetch_all and capture (sql, params).
"""

from __future__ import annotations

import pytest

from app.workflows.consolidate_agent_memory import (
    _ACTIVE_PAIRS_SQL,
    _PAIR_CONTEXTS_SQL,
    _RECENT_MESSAGES_SQL,
    MIN_NEW_MESSAGES,
    _consolidate_context,
    enumerate_active_pairs_step,
)

_LEGACY_TABLES = ("ai_sessions", "ai_messages")


# ---------------------------------------------------------------------------
# (a) SQL constants target conversations/messages, never legacy tables
# ---------------------------------------------------------------------------


def test_active_pairs_sql_targets_conversations_not_legacy():
    sql = _ACTIVE_PAIRS_SQL
    for legacy in _LEGACY_TABLES:
        assert legacy not in sql, f"_ACTIVE_PAIRS_SQL must not reference {legacy}"
    assert "public.conversations" in sql
    assert "public.conversation_ai_meta" in sql
    assert "public.conversation_members" in sql
    assert "public.messages" in sql
    assert "member_type = 'user'" in sql
    assert "c.type        = 'direct_agent'" in sql or "c.type = 'direct_agent'" in sql
    assert "c.archived_at" in sql


def test_recent_messages_sql_targets_conversations_not_legacy():
    sql = _RECENT_MESSAGES_SQL
    for legacy in _LEGACY_TABLES:
        assert legacy not in sql, f"_RECENT_MESSAGES_SQL must not reference {legacy}"
    assert "public.messages" in sql
    assert "public.conversations" in sql
    assert "public.conversation_ai_meta" in sql
    assert "public.conversation_members" in sql


def test_pair_contexts_sql_targets_conversations_not_legacy():
    sql = _PAIR_CONTEXTS_SQL
    for legacy in _LEGACY_TABLES:
        assert legacy not in sql, f"_PAIR_CONTEXTS_SQL must not reference {legacy}"
    assert "public.conversations" in sql
    assert "public.conversation_ai_meta" in sql
    assert "public.conversation_members" in sql


# ---------------------------------------------------------------------------
# (b) role/content mapping
# ---------------------------------------------------------------------------


def test_recent_messages_sql_maps_agent_sender_to_assistant_role():
    sql = _RECENT_MESSAGES_SQL
    assert "sender_type = 'agent'" in sql
    assert "'assistant'" in sql
    assert "AS role" in sql


def test_recent_messages_sql_reads_body_text_as_content():
    sql = _RECENT_MESSAGES_SQL
    assert "body->>'text'" in sql
    assert "AS content" in sql
    assert "deleted_at IS NULL" in sql


def test_active_pairs_sql_filters_deleted_messages():
    assert "deleted_at IS NULL" in _ACTIVE_PAIRS_SQL


# ---------------------------------------------------------------------------
# (c) grouping / threshold semantics preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enumerate_active_pairs_still_filters_by_min_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MIN_NEW_MESSAGES python-side filter is unaffected by the SQL
    rewrite — it still operates on the msg_count column the new query
    computes via COUNT(msg.id)."""
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

    captured: dict = {}

    async def fake_fetch_all(sql: str, params=None) -> list:
        captured["sql"] = sql
        return rows

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)

    pairs = await enumerate_active_pairs_step()

    # The query actually executed must be the new conversations-only SQL.
    assert captured["sql"] is _ACTIVE_PAIRS_SQL
    assert len(pairs) == 1
    assert pairs[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_consolidate_context_recent_messages_query_is_new_sql_with_null_safe_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_context's first fetch_all call must be the new
    _RECENT_MESSAGES_SQL, carrying user_id/agent_id/team_id/project_id
    (NULL-safe personal-scope context) exactly like the legacy call did."""
    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    fetch_calls: list[dict] = []

    async def fake_fetch_all(sql: str, params=None) -> list:
        fetch_calls.append({"sql": sql, "params": dict(params) if params else {}})
        if len(fetch_calls) == 1:
            return messages  # _RECENT_MESSAGES_SQL
        return []  # _EXISTING_TITLES_SQL

    async def fake_existing_fps(**kwargs) -> set:
        return set()

    async def fake_write(**kwargs) -> bool:
        return True

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return "[]"  # no drafts — keeps this test focused on the read path

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
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
    assert len(fetch_calls) == 2
    first = fetch_calls[0]
    assert first["sql"] is _RECENT_MESSAGES_SQL
    assert first["params"] == {
        "user_id": "u1",
        "agent_id": "a1",
        "team_id": None,
        "project_id": None,
    }


@pytest.mark.asyncio
async def test_consolidate_context_recent_messages_query_carries_team_project_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team+project context passes team_id/project_id through unchanged
    to the new _RECENT_MESSAGES_SQL — same NULL-safe param contract as
    before the store rewrite."""
    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]
    fetch_calls: list[dict] = []

    async def fake_fetch_all(sql: str, params=None) -> list:
        fetch_calls.append({"sql": sql, "params": dict(params) if params else {}})
        return messages if len(fetch_calls) == 1 else []

    async def fake_existing_fps(**kwargs) -> set:
        return set()

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return "[]"

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )

    await _consolidate_context("u1", "a1", team_id=10, project_id=55)

    first_params = fetch_calls[0]["params"]
    assert first_params["team_id"] == 10
    assert first_params["project_id"] == 55


@pytest.mark.asyncio
async def test_consolidate_context_below_threshold_still_skips_with_new_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threshold semantics (MIN_NEW_MESSAGES) are unchanged by the store
    rewrite: too few rows from the new _RECENT_MESSAGES_SQL still yields
    an early no-write return."""
    few_messages = [{"role": "user", "content": "hi"}] * (MIN_NEW_MESSAGES - 1)
    write_called = [False]

    async def fake_fetch_all(sql: str, params=None) -> list:
        return few_messages

    async def fake_write(**kwargs) -> bool:
        write_called[0] = True
        return True

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row",
        fake_write,
    )

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    assert result["written"] == 0
    assert not write_called[0]
