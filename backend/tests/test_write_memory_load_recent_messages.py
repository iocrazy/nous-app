"""load_recent_messages_step — store-aware message loading (Phase 2 fix).

A "session id" may be a conversations.id (direct_agent, flag=on) or a legacy
ai_sessions.id. The step probes conversations first; conversation rows map
sender_type→role and body->>'text'→content. Missed by the original parity
inventory (only this workflow's S8 team lookup was catalogued), surfaced by
the Phase-3 scout: with flag=on, memory extraction for new-store sessions
silently read zero rows from ai_messages.

Harness mirrors test_write_memory_resolve_team_workspace.py: monkeypatch the
module-level app.db.engine fetch helpers.
"""

from __future__ import annotations

import pytest

from app.workflows.write_memory import load_recent_messages_step


def _install(monkeypatch, *, is_conversation: bool, rows: list[dict]):
    captured: dict = {}

    async def fake_fetch_one(sql: str, params=None):
        captured["probe_sql"] = sql
        captured["probe_params"] = params
        return {"x": 1} if is_conversation else None

    async def fake_fetch_all(sql: str, params=None):
        captured["rows_sql"] = sql
        captured["rows_params"] = params
        return rows

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    return captured


@pytest.mark.asyncio
async def test_conversation_id_reads_messages_with_role_content_mapping(monkeypatch):
    captured = _install(
        monkeypatch,
        is_conversation=True,
        rows=[
            {"role": "assistant", "content": "reply two"},
            {"role": "user", "content": "question two"},
        ],
    )
    out = await load_recent_messages_step("323848780659604")

    assert "FROM public.conversations" in captured["probe_sql"]
    sql = captured["rows_sql"]
    assert "FROM public.messages" in sql
    assert "sender_type = 'agent'" in sql and "'assistant'" in sql
    assert "body->>'text'" in sql
    assert "deleted_at IS NULL" in sql
    assert "ORDER BY seq DESC" in sql
    assert captured["rows_params"]["sid"] == 323848780659604
    assert out["user_msgs"] == ["question two"]
    assert out["asst_msgs"] == ["reply two"]


@pytest.mark.asyncio
async def test_legacy_id_falls_back_to_ai_messages(monkeypatch):
    captured = _install(
        monkeypatch,
        is_conversation=False,
        rows=[{"role": "user", "content": "old question"}],
    )
    out = await load_recent_messages_step("123456")

    assert "FROM public.ai_messages" in captured["rows_sql"]
    assert "ORDER BY created_at DESC" in captured["rows_sql"]
    assert captured["rows_params"]["sid"] == 123456
    assert out["user_msgs"] == ["old question"]
    assert out["asst_msgs"] == []
