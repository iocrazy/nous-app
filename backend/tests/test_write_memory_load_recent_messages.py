"""load_recent_messages_step — conversations-only message loading.

Conversations Phase 3, Task 6 collapsed the compatibility layer: the
legacy ``ai_messages`` fallback this step used to try when a session id
wasn't found on ``conversations`` is gone (the legacy table itself is
dropped in Wave 2). Conversation rows map sender_type→role and
body->>'text'→content.

ORM (Phase B4): the raw ``fetch_all`` call became a
``select(case(...), func.coalesce(Messages.body["text"].astext, ""))``
through ``app.db.session.read_scope()`` — the harness patches read_scope and
inspects the compiled statement instead of the raw SQL string.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from app.workflows.write_memory import load_recent_messages_step


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, rows: list[dict]) -> None:
        self._result = _FakeResult(rows)
        self.calls: list = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.calls.append((str(compiled), dict(compiled.params)))
        return self._result


def _install(monkeypatch, *, rows: list[dict]) -> _RecordingSession:
    import app.db.session as db_session

    session = _RecordingSession(rows)

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)
    return session


@pytest.mark.asyncio
async def test_conversation_id_reads_messages_with_role_content_mapping(monkeypatch):
    session = _install(
        monkeypatch,
        rows=[
            {"role": "assistant", "content": "reply two"},
            {"role": "user", "content": "question two"},
        ],
    )
    out = await load_recent_messages_step("323848780659604")

    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "FROM public.messages" in sql
    assert (
        "sender_type = %(sender_type_1)s" in sql and binds["sender_type_1"] == "agent"
    )
    assert binds["param_1"] == "assistant"
    assert "body ->> " in sql
    assert "deleted_at IS NULL" in sql
    assert "ORDER BY public.messages.seq DESC" in sql
    assert binds["conversation_id_1"] == 323848780659604
    assert out["user_msgs"] == ["question two"]
    assert out["asst_msgs"] == ["reply two"]
