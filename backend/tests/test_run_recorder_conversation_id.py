"""RunRecorder must carry conversation_id into the agent_runs INSERT (Phase 1.5:
structural run→conversation link, was metadata-json only).

RunRecorder._insert_row now issues a raw ``text()`` INSERT via
``app.db.session.write_scope`` (SQLAlchemy ORM session), not the old
supabase-py ``.table("agent_runs").insert(...)`` fluent call. The fake
session below captures the bound params dict passed to ``execute()``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4


class _FakeResult:
    def __init__(self, row_id: int) -> None:
        self._row_id = (row_id,)

    def first(self):
        return self._row_id


class _FakeSession:
    def __init__(self, captured: dict, row_id: int) -> None:
        self._captured = captured
        self._row_id = row_id

    async def execute(self, stmt, params=None):
        self._captured["payload"] = dict(params or {})
        return _FakeResult(self._row_id)


def _write_scope(captured: dict, row_id: int = 900000000000001):
    @asynccontextmanager
    async def _scope():
        yield _FakeSession(captured, row_id)

    return _scope


async def test_insert_row_includes_conversation_id():
    from app.services.ai.runner.run_recorder import RunRecorder

    captured: dict = {}

    with patch("app.db.session.write_scope", _write_scope(captured)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat_summon",
            conversation_id=123456789,
        )
        await rec._insert_row()

    assert captured["payload"]["conversation_id"] == 123456789


async def test_conversation_id_defaults_none():
    from app.services.ai.runner.run_recorder import RunRecorder

    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    assert rec.conversation_id is None


async def test_insert_row_omits_conversation_id_when_not_set():
    from app.services.ai.runner.run_recorder import RunRecorder

    captured: dict = {}

    with patch("app.db.session.write_scope", _write_scope(captured)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat",
        )
        await rec._insert_row()

    assert "conversation_id" not in captured["payload"]
