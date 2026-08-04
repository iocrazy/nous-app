"""Verify list_accessible_for_user filters by ownership + team membership + q + kinds.

Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-orm-
full-migration.md): the query moved off db_engine.fetch_all text() SQL onto
an ORM ``select(...)`` over ``read_scope()``. These tests patch at that
boundary — a fake session captures the compiled statement (Postgres dialect,
literal binds) so the same assertions (ILIKE present, kinds_re bound values,
limit capping, team-membership subquery, scope_team_id narrowing) still hold
against the new implementation, without touching a real DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.repositories.resources_repository import ResourcesRepository

pytestmark = pytest.mark.asyncio


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _CapturingSession:
    """Captures the compiled SELECT (Postgres dialect, literal binds)."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.captured_sql: str | None = None

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        compiled = stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        self.captured_sql = str(compiled)
        return _FakeResult(self._rows)


def _patch_read_scope(monkeypatch, session):
    # ResourcesRepository imports read_scope at MODULE level
    # (`from app.db.session import read_scope`), so the name is already
    # bound in the repository module's own namespace — patch it there, not
    # on app.db.session (which would miss the already-bound reference).
    import app.repositories.resources_repository as repo_module

    @asynccontextmanager
    async def _read_scope():
        yield session

    monkeypatch.setattr(repo_module, "read_scope", _read_scope)


def _patch_system_request_scope_noop(monkeypatch):
    import app.repositories.resources_repository as repo_module

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(repo_module, "system_request_scope", _system_request_scope)


def _capture(monkeypatch) -> _CapturingSession:
    session = _CapturingSession()
    _patch_system_request_scope_noop(monkeypatch)
    _patch_read_scope(monkeypatch, session)
    return session


async def test_accepts_q_and_kinds_and_limit(monkeypatch):
    repo = ResourcesRepository()
    session = _capture(monkeypatch)

    await repo.list_accessible_for_user(
        user_id="user-1",
        q="story",
        kinds=["video", "image"],
        limit=20,
    )

    sql = session.captured_sql.lower()
    assert "ilike" in sql
    assert "'user-1'" in sql
    assert "%story%" in sql
    assert "^video/" in sql
    assert "^image/" in sql
    assert "limit 20" in sql


async def test_default_limit_is_20_and_caps_at_50(monkeypatch):
    repo = ResourcesRepository()
    session = _capture(monkeypatch)

    await repo.list_accessible_for_user(user_id="u", limit=999)

    assert "limit 50" in session.captured_sql.lower()  # capped


async def test_scope_team_id_narrows_to_team_plus_personal(monkeypatch):
    repo = ResourcesRepository()
    session = _capture(monkeypatch)

    await repo.list_accessible_for_user(
        user_id="user-1",
        scope_team_id="900123",
    )

    sql = session.captured_sql.lower()
    # Still gated on membership (no escalation via a forged team_id)…
    assert "team_members" in sql
    # …but now narrowed to the passed team OR the caller's personal team.
    assert "'900123'" in sql
    assert "kind = 'personal'" in sql


async def test_scope_team_id_omitted_keeps_all_teams(monkeypatch):
    repo = ResourcesRepository()
    session = _capture(monkeypatch)

    await repo.list_accessible_for_user(user_id="user-1")

    sql = session.captured_sql.lower()
    assert "900123" not in sql
    assert "team_members" in sql


async def test_returns_empty_list_when_no_rows(monkeypatch):
    repo = ResourcesRepository()
    _capture(monkeypatch)

    rows = await repo.list_accessible_for_user(user_id="user-1")
    assert rows == []


async def test_returns_row_dicts(monkeypatch):
    repo = ResourcesRepository()
    session = _CapturingSession(
        rows=[
            {
                "id": "1",
                "name": "a.mp4",
                "mime": "video/mp4",
                "size": 100,
                "updated_at": "2026-08-01T00:00:00Z",
                "scope_id": "9",
                "scope_type": "personal",
            }
        ]
    )
    _patch_system_request_scope_noop(monkeypatch)
    _patch_read_scope(monkeypatch, session)

    rows = await repo.list_accessible_for_user(user_id="user-1")
    assert rows == [
        {
            "id": "1",
            "name": "a.mp4",
            "mime": "video/mp4",
            "size": 100,
            "updated_at": "2026-08-01T00:00:00Z",
            "scope_id": "9",
            "scope_type": "personal",
        }
    ]
