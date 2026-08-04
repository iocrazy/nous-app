"""ORM equivalence tests for ``resource_ref_resolver._fetch_accessible_meta``
(Phase A raw-SQL-to-ORM migration,
docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md).

The public ``resolve_resource_refs`` tests (test_resource_ref_resolver.py)
mock ``_fetch_accessible_meta`` entirely, so they don't exercise the query
itself. These tests do, via a fake ORM session that captures the compiled
statement (Postgres dialect, literal binds) — no real DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services.ai.chat.resource_ref_resolver import _fetch_accessible_meta

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _CapturingSession:
    def __init__(self, rows):
        self._rows = rows
        self.captured_sql: str | None = None

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        compiled = stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        self.captured_sql = str(compiled)

        class _Result:
            def mappings(_self):
                return _FakeMappingsResult(self._rows)

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


async def test_empty_resource_ids_short_circuits_no_query(monkeypatch):
    """No resource_ids → {} without opening a session at all."""

    class _MustNotOpen:
        async def execute(self, _stmt):
            raise AssertionError("must not query DB for empty resource_ids")

    _patch_scopes(monkeypatch, _MustNotOpen())
    result = await _fetch_accessible_meta("u1", [])
    assert result == {}


async def test_compiled_sql_matches_legacy_shape(monkeypatch):
    session = _CapturingSession(rows=[])
    _patch_scopes(monkeypatch, session)

    await _fetch_accessible_meta("u1", ["1", "2"])

    sql = session.captured_sql
    assert "resources" in sql
    assert "resource_items" in sql
    assert "team_members" in sql
    assert "'1', '2'" in sql or "'2', '1'" in sql
    assert "'u1'" in sql
    assert "is_trashed IS false" in sql


async def test_personal_scope_row_maps_correctly(monkeypatch):
    session = _CapturingSession(
        rows=[
            {
                "id": "1",
                "name": "a.md",
                "mime": "text/markdown",
                "size": 100,
                "brief": None,
                "updated_at": "2026-08-01T00:00:00Z",
                "scope_id": "9",
                "team_name": None,
                "scope_kind": "personal",
            }
        ]
    )
    _patch_scopes(monkeypatch, session)

    result = await _fetch_accessible_meta("u1", ["1"])
    assert result["1"]["scope"] == "personal"
    assert result["1"]["kind"] == "doc"


async def test_team_scope_row_uses_team_name_or_falls_back_to_scope_id(monkeypatch):
    session = _CapturingSession(
        rows=[
            {
                "id": "1",
                "name": "a.md",
                "mime": "text/markdown",
                "size": 100,
                "brief": None,
                "updated_at": "2026-08-01T00:00:00Z",
                "scope_id": "9",
                "team_name": "alpha",
                "scope_kind": "collaborative",
            },
            {
                "id": "2",
                "name": "b.md",
                "mime": "text/markdown",
                "size": 100,
                "brief": None,
                "updated_at": "2026-08-01T00:00:00Z",
                "scope_id": "10",
                "team_name": None,
                "scope_kind": "collaborative",
            },
        ]
    )
    _patch_scopes(monkeypatch, session)

    result = await _fetch_accessible_meta("u1", ["1", "2"])
    assert result["1"]["scope"] == "team:alpha"
    assert result["2"]["scope"] == "team:10"


async def test_missing_ids_are_simply_absent_from_result(monkeypatch):
    session = _CapturingSession(rows=[])
    _patch_scopes(monkeypatch, session)

    result = await _fetch_accessible_meta("u1", ["999"])
    assert result == {}
