"""Tests for the resource_fetch channel team-scope param (CHAT-SEC-AGENT-03).

Verifies that:
  (a) When team_id is set, a resource whose scope_id != team_id is rejected
      (PermissionError) even if the summoner's membership would ordinarily
      allow it — the query includes the extra team filter.
  (b) When team_id=None, the query is unchanged — no team-scope filter is
      added — preserving the existing ai_library chat path exactly.

Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-orm-
full-migration.md): the access-check query moved off db_engine.fetch_all
text() SQL onto an ORM ``select(...)`` over ``read_scope()``. These tests
now patch at that boundary — a fake session captures the compiled statement
so the same assertions (team filter present when team_id is set, absent
otherwise, membership subquery always present) still hold against the new
implementation, without touching a real DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _CapturingSession:
    """Captures the compiled SELECT (Postgres dialect, literal binds) so
    tests can assert on the SQL shape without depending on SQLAlchemy's
    internal Select repr."""

    def __init__(self, rows):
        self._rows = rows
        self.captured_sql: str | None = None

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        compiled = stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        self.captured_sql = str(compiled)
        return _FakeResult(self._rows)


def _patch_read_scope(monkeypatch, session):
    import app.db.session as session_module

    @asynccontextmanager
    async def _read_scope():
        yield session

    monkeypatch.setattr(session_module, "read_scope", _read_scope)


def _patch_system_request_scope_noop(monkeypatch):
    import app.db.scope as scope_module

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)


# ---------------------------------------------------------------------------
# (a) With team_id set — resource in wrong scope raises PermissionError
# ---------------------------------------------------------------------------


async def test_fetch_dispatch_team_scope_rejects_wrong_scope(monkeypatch):
    """No row (as the DB would return when scope_id != team_id) →
    _fetch_dispatch must raise PermissionError."""
    from app.services.ai.tools.resource_fetch_tool import _fetch_dispatch

    _patch_system_request_scope_noop(monkeypatch)
    _patch_read_scope(monkeypatch, _CapturingSession([]))

    with pytest.raises(PermissionError):
        await _fetch_dispatch(
            resource_id="111",
            mode=None,
            args=None,
            user_id="user-abc",
            team_id=999,
        )


# ---------------------------------------------------------------------------
# (b) With team_id set — compiled SQL includes the team-scope filter
# ---------------------------------------------------------------------------


async def test_fetch_dispatch_team_scope_sql_includes_team_filter(monkeypatch):
    """When team_id is provided, the compiled SELECT must filter
    resource_items.scope_id to that team, alongside the membership
    subquery (not a replacement for it)."""
    from app.services.ai.tools.resource_fetch_tool import _fetch_dispatch

    _patch_system_request_scope_noop(monkeypatch)
    session = _CapturingSession([])
    _patch_read_scope(monkeypatch, session)

    with pytest.raises(PermissionError):
        await _fetch_dispatch(
            resource_id="222",
            mode=None,
            args=None,
            user_id="user-abc",
            team_id=42,
        )

    assert session.captured_sql is not None, "session.execute was not called"
    sql = session.captured_sql
    assert "'42'" in sql, "compiled SQL must bind the team_id filter"
    assert "resource_items" in sql
    assert "team_members" in sql, "membership subquery must still be present"
    assert "'user-abc'" in sql, "membership subquery must filter by user_id"


# ---------------------------------------------------------------------------
# (c) With team_id=None — no team-scope filter added
# ---------------------------------------------------------------------------


async def test_fetch_dispatch_no_team_filter_when_team_id_none(monkeypatch):
    """When team_id=None, the compiled SQL must not carry an extra
    equality filter on resource_items.scope_id beyond the membership
    IN-subquery — preserving exact current behaviour for the ai_library
    chat path."""
    from app.services.ai.tools.resource_fetch_tool import _fetch_dispatch

    _patch_system_request_scope_noop(monkeypatch)
    session = _CapturingSession([])
    _patch_read_scope(monkeypatch, session)

    with pytest.raises(PermissionError):
        await _fetch_dispatch(
            resource_id="333",
            mode=None,
            args=None,
            user_id="user-abc",
            team_id=None,
        )

    assert session.captured_sql is not None
    sql = session.captured_sql
    assert "team_members" in sql
    # Only ONE scope_id comparison (the membership IN-subquery) — no second
    # standalone `= '<team>'` equality the team_id branch would add.
    assert sql.count("resource_items.scope_id") <= 2  # column ref + IN clause


# ---------------------------------------------------------------------------
# (d) resource_fetch public API threads team_id down to _fetch_dispatch
# ---------------------------------------------------------------------------


async def test_resource_fetch_threads_team_id_to_dispatch(monkeypatch):
    """resource_fetch must accept team_id and forward it to _fetch_dispatch.
    When the dispatch raises PermissionError the public wrapper converts it
    to {"error": "resource not accessible"} (existing catch block)."""
    from app.services.ai.tools import resource_fetch_tool as m

    captured_team_ids: list[int | None] = []

    async def _fake_dispatch(*, resource_id, mode, args, user_id, team_id=None):
        captured_team_ids.append(team_id)
        raise PermissionError("no access")

    monkeypatch.setattr(m, "_fetch_dispatch", _fake_dispatch)

    result = await m.resource_fetch(
        resource_id="444",
        mode=None,
        args=None,
        user_id="user-abc",
        available_refs={"444"},
        request_cache={},
        team_id=77,
    )

    assert result == {"error": "resource not accessible"}
    assert captured_team_ids == [
        77
    ], "resource_fetch must pass team_id=77 through to _fetch_dispatch"


# ---------------------------------------------------------------------------
# (e) team_id=None via resource_fetch — back-compat for ai_library callers
# ---------------------------------------------------------------------------


async def test_resource_fetch_team_id_defaults_to_none(monkeypatch):
    """Existing callers that don't pass team_id must still work — the arg is
    optional and defaults to None, and _fetch_dispatch receives None."""
    from app.services.ai.tools import resource_fetch_tool as m

    captured_team_ids: list[int | None] = []

    async def _fake_dispatch(*, resource_id, mode, args, user_id, team_id=None):
        captured_team_ids.append(team_id)
        raise PermissionError("no access")

    monkeypatch.setattr(m, "_fetch_dispatch", _fake_dispatch)

    # Deliberately omit team_id to test back-compat
    result = await m.resource_fetch(
        resource_id="555",
        mode=None,
        args=None,
        user_id="user-abc",
        available_refs={"555"},
        request_cache={},
    )

    assert result == {"error": "resource not accessible"}
    assert captured_team_ids == [None], "team_id must default to None when not supplied"
