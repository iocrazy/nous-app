"""``write_memory._resolve_team_workspace`` — conversations-only lookup.

Conversations Phase 3, Task 6 collapsed the compatibility layer: the
legacy ``ai_sessions`` fallback this function used to try after a
conversations miss is gone (the legacy table itself is dropped in Wave 2).

The conversations column is ``scope_id`` (aliased ``AS team_id`` via the ORM
label), NOT a literal ``team_id`` column — that was the ai_sessions-only
column name.

ORM (Phase B4): the raw ``fetch_one`` call became a
``select(Conversations.scope_id.label("team_id"))`` through
``app.db.session.read_scope()`` — the harness patches read_scope instead of
the raw engine helper.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql


class _FakeResult:
    def __init__(self, rows: list | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    def __init__(self, result: _FakeResult) -> None:
        self._result = result
        self.calls: list = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.calls.append((str(compiled), dict(compiled.params)))
        return self._result


def _patch_read_scope(monkeypatch: pytest.MonkeyPatch, session: _RecordingSession):
    import app.db.session as db_session

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)


@pytest.mark.asyncio
async def test_resolve_team_workspace_reads_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    session = _RecordingSession(_FakeResult(rows=[{"team_id": 900000000000001}]))
    _patch_read_scope(monkeypatch, session)

    workspace = await write_memory._resolve_team_workspace("888")

    assert workspace == "team-900000000000001"
    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "public.conversations" in sql
    assert "AS team_id" in sql
    assert binds["id_1"] == 888


@pytest.mark.asyncio
async def test_resolve_team_workspace_returns_none_when_no_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    session = _RecordingSession(_FakeResult(rows=[{"team_id": None}]))
    _patch_read_scope(monkeypatch, session)

    assert await write_memory._resolve_team_workspace("888") is None


@pytest.mark.asyncio
async def test_resolve_team_workspace_returns_none_when_session_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import write_memory

    session = _RecordingSession(_FakeResult(rows=[]))
    _patch_read_scope(monkeypatch, session)

    assert await write_memory._resolve_team_workspace("888") is None


@pytest.mark.asyncio
async def test_resolve_team_workspace_degrades_to_none_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any failure (bad session_id, DB outage) must degrade to None
    (deployment default workspace) — never raise, never block the write."""
    from app.workflows import write_memory

    assert await write_memory._resolve_team_workspace("not-an-int") is None
