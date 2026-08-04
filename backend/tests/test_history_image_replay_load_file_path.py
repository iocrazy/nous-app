"""ORM equivalence tests for ``history_image_replay._load_file_path_db``
(Phase A raw-SQL-to-ORM migration,
docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md).

The existing test_history_image_replay.py tests inject a custom
``load_file_path`` callable and never exercise the DB-backed default, so
these pin the ORM query directly against a fake session (no real DB).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services.ai.chat.history_image_replay import _load_file_path_db

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeScalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _CapturingSession:
    def __init__(self, value):
        self._value = value
        self.captured_sql: str | None = None

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        compiled = stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        self.captured_sql = str(compiled)

        class _Result:
            def scalars(_self):
                return _FakeScalars(self._value)

        return _Result()


def _patch_scopes(monkeypatch, session):
    import app.db.scope as scope_module
    import app.db.session as session_module

    @asynccontextmanager
    async def _read_scope():
        yield session

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(session_module, "read_scope", _read_scope)
    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)


async def test_row_found_returns_file_path(monkeypatch):
    session = _CapturingSession("sb://library/t5/img/1.jpg")
    _patch_scopes(monkeypatch, session)

    result = await _load_file_path_db("1", "u1")
    assert result == "sb://library/t5/img/1.jpg"

    sql = session.captured_sql
    assert "resources" in sql
    assert "resource_items" in sql
    assert "team_members" in sql
    assert "'1'" in sql
    assert "'u1'" in sql
    assert "is_trashed IS false" in sql


async def test_no_row_returns_none(monkeypatch):
    session = _CapturingSession(None)
    _patch_scopes(monkeypatch, session)

    result = await _load_file_path_db("999", "u1")
    assert result is None


async def test_row_with_empty_file_path_returns_none(monkeypatch):
    session = _CapturingSession("")
    _patch_scopes(monkeypatch, session)

    result = await _load_file_path_db("1", "u1")
    assert result is None
