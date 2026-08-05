"""``chat_upload._get_session_team_id`` — conversations-only lookup.

Conversations Phase 3, Task 6 collapsed the compatibility layer: the
legacy ``ai_sessions`` fallback this used to try after a conversations
miss is gone (the legacy table itself is dropped in Wave 2).

ORM (Phase B4): the raw ``fetch_one`` call became a
``select(Conversations.scope_id).where(...)`` through
``app.db.session.read_scope()`` — the harness patches read_scope and
inspects the compiled statement instead of the raw SQL string.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:  # noqa: D102
        return self._row


class _RecordingSession:
    def __init__(self, row: Any) -> None:
        self._result = _FakeResult(row)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any) -> Any:  # noqa: D102
        self.calls.append(_compile(stmt))
        return self._result


def _install(monkeypatch: pytest.MonkeyPatch, *, row: Any) -> _RecordingSession:
    import app.db.session as db_session

    session = _RecordingSession(row)

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)
    return session


@pytest.mark.asyncio
async def test_get_session_team_id_reads_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.library import chat_upload as m

    session = _install(monkeypatch, row=(900000000000001,))

    team_id = await m._get_session_team_id("888")

    assert team_id == 900000000000001
    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "public.conversations" in sql
    assert "public.conversations.scope_id" in sql
    assert binds["id_1"] == 888


@pytest.mark.asyncio
async def test_get_session_team_id_returns_none_when_session_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.library import chat_upload as m

    _install(monkeypatch, row=None)

    assert await m._get_session_team_id("888") is None


@pytest.mark.asyncio
async def test_get_session_team_id_returns_none_when_null_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A found conversations row with a NULL scope_id (shouldn't happen for
    a direct_agent session, but defensive) returns None."""
    from app.services.library import chat_upload as m

    _install(monkeypatch, row=(None,))

    assert await m._get_session_team_id("888") is None


@pytest.mark.asyncio
async def test_get_session_team_id_conversations_row_honored_by_resolve_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a session_id resolves to ("team", str(team_id)) via
    resolve_chat_scope (a personal conversation's scope_id IS a team_id —
    Phase 2 assigns personal-team scope at create)."""
    from app.services.library import chat_upload as m

    _install(monkeypatch, row=(7001,))

    scope = await m.resolve_chat_scope(session_id="888", user_id="u1")

    assert scope == ("team", "7001")
