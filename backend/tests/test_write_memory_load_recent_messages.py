"""load_recent_messages_step — conversations-only message loading.

Conversations Phase 3, Task 6 collapsed the compatibility layer: the
legacy ``ai_messages`` fallback this step used to try when a session id
wasn't found on ``conversations`` is gone (the legacy table itself is
dropped in Wave 2). Conversation rows map sender_type→role and
body->>'text'→content.

Harness mirrors test_write_memory_resolve_team_workspace.py: monkeypatch the
module-level app.db.engine fetch helpers.
"""

from __future__ import annotations

import pytest

from app.workflows.write_memory import load_recent_messages_step


def _install(monkeypatch, *, rows: list[dict]):
    captured: dict = {}

    async def fake_fetch_all(sql: str, params=None):
        captured["rows_sql"] = sql
        captured["rows_params"] = params
        return rows

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    return captured


@pytest.mark.asyncio
async def test_conversation_id_reads_messages_with_role_content_mapping(monkeypatch):
    captured = _install(
        monkeypatch,
        rows=[
            {"role": "assistant", "content": "reply two"},
            {"role": "user", "content": "question two"},
        ],
    )
    out = await load_recent_messages_step("323848780659604")

    sql = captured["rows_sql"]
    assert "FROM public.messages" in sql
    assert "sender_type = 'agent'" in sql and "'assistant'" in sql
    assert "body->>'text'" in sql
    assert "deleted_at IS NULL" in sql
    assert "ORDER BY seq DESC" in sql
    assert captured["rows_params"]["sid"] == 323848780659604
    assert out["user_msgs"] == ["question two"]
    assert out["asst_msgs"] == ["reply two"]
