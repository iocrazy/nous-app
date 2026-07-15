"""RunRecorder must carry issue_id into the agent_runs INSERT so mig-208's
trigger emits + later updates the issue chat row.

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


async def test_insert_row_includes_issue_id():
    from app.services.ai.runner.run_recorder import RunRecorder

    captured: dict = {}

    with patch("app.db.session.write_scope", _write_scope(captured)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="issue_dispatch",
            issue_id=409,
        )
        await rec._insert_row()

    assert captured["payload"]["issue_id"] == 409


async def test_insert_row_omits_issue_id_when_not_set():
    from app.services.ai.runner.run_recorder import RunRecorder

    captured: dict = {}

    with patch("app.db.session.write_scope", _write_scope(captured)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat",
        )
        await rec._insert_row()

    assert "issue_id" not in captured["payload"]
