"""get_or_create_issue_session returns the issue's session, creating one on
first call and reusing it after.

Post-Phase-B1 ORM rewrite: ``issue_session.py`` no longer calls
``app.db.engine.fetch_one``/``execute`` — reads/writes go through
``read_scope()``/``write_scope()`` (SQLAlchemy Core ``select``/``update``
against the ``Issues`` model). Tests patch those scopes on their source
module (``app.db.session``), since ``issue_session.py`` does a fresh
function-scope import each call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.db import session as db_session


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    try:
        compiled = stmt.compile(dialect=postgresql.dialect())
        return str(compiled), dict(compiled.params)
    except Exception:
        return str(stmt), {}


class _FakeResult:
    """Supports the two shapes issue_session.py consumes: ``.mappings().first()``
    (the SELECT of several columns) and ``.scalar_one_or_none()`` (the single-
    column winner SELECT)."""

    def __init__(self, mapping: dict | None = None, scalar: Any = None) -> None:
        self._mapping = mapping
        self._scalar = scalar
        self.rowcount = 1 if mapping is not None else 0

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict | None:
        return self._mapping

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _FakeSession:
    def __init__(self, *, select_row: dict | None, rowcount: int = 1) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._select_row = select_row
        self._rowcount = rowcount
        self.winner_ai_session_id: Any = None

    async def execute(self, stmt: Any) -> _FakeResult:
        sql, binds = _compile(stmt)
        self.calls.append((sql, binds))
        if sql.startswith("SELECT public.issues.ai_session_id, "):
            return _FakeResult(mapping=self._select_row)
        if sql.startswith("UPDATE public.issues SET ai_session_id="):
            result = _FakeResult()
            result.rowcount = self._rowcount
            return result
        if sql.startswith("SELECT public.issues.ai_session_id \n"):
            return _FakeResult(scalar=self.winner_ai_session_id)
        return _FakeResult()


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_scopes(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))


async def test_returns_existing_when_issue_has_session(monkeypatch):
    from app.services.issues import issue_session as m

    sid = str(uuid4())
    session = _FakeSession(
        select_row={
            "ai_session_id": sid,
            "title": "t",
            "assignee_agent_id": str(uuid4()),
            "created_by_user_id": str(uuid4()),
            "assignee_user_id": None,
            "project_id": None,
            "team_id": None,
        }
    )
    _patch_scopes(monkeypatch, session)

    got = await m.get_or_create_issue_session(409)
    assert got == sid
    # No write should happen — the session already existed.
    assert not any("UPDATE" in sql for sql, _ in session.calls)


async def test_creates_and_backfills_when_absent(monkeypatch):
    from app.services.issues import issue_session as m

    agent_uuid, user_uuid = str(uuid4()), str(uuid4())
    session = _FakeSession(
        select_row={
            "ai_session_id": None,
            "title": "Write essay",
            "assignee_agent_id": agent_uuid,
            "created_by_user_id": user_uuid,
            "assignee_user_id": None,
            "project_id": None,
            "team_id": None,
        },
        rowcount=1,
    )
    _patch_scopes(monkeypatch, session)

    chat_svc = AsyncMock()
    # ai_sessions.id is a BIGINT snowflake since mig 232 (service returns it
    # as a str) — the backfill must coerce to int for asyncpg.
    chat_svc.create_session = AsyncMock(return_value={"id": "315917457926636"})
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    agent_repo = AsyncMock()
    agent_repo.get_by_id = AsyncMock(return_value={"slug": "writer"})
    monkeypatch.setattr(m, "get_agent_repository", lambda: agent_repo)

    got = await m.get_or_create_issue_session(409)

    assert got == "315917457926636"
    update_calls = [(sql, binds) for sql, binds in session.calls if "UPDATE" in sql]
    assert len(update_calls) == 1
    sql, binds = update_calls[0]
    assert "UPDATE public.issues SET ai_session_id=" in sql
    assert "public.issues.id = " in sql
    assert "public.issues.ai_session_id IS NULL" in sql
    # sid must be an int (BIGINT column; asyncpg rejects str)
    assert 315917457926636 in binds.values()
    assert 409 in binds.values()


async def test_backfill_race_returns_winners_session(monkeypatch):
    """0 rows updated (a concurrent create already won) → fall back to a
    plain SELECT of the winner's ai_session_id."""
    from app.services.issues import issue_session as m

    agent_uuid, user_uuid = str(uuid4()), str(uuid4())
    session = _FakeSession(
        select_row={
            "ai_session_id": None,
            "title": "t",
            "assignee_agent_id": agent_uuid,
            "created_by_user_id": user_uuid,
            "assignee_user_id": None,
            "project_id": None,
            "team_id": None,
        },
        rowcount=0,
    )
    session.winner_ai_session_id = 555
    _patch_scopes(monkeypatch, session)

    chat_svc = AsyncMock()
    chat_svc.create_session = AsyncMock(return_value={"id": "999"})
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    agent_repo = AsyncMock()
    agent_repo.get_by_id = AsyncMock(return_value={"slug": "writer"})
    monkeypatch.setattr(m, "get_agent_repository", lambda: agent_repo)

    got = await m.get_or_create_issue_session(409)
    assert got == "555"


async def test_returns_none_when_no_agent(monkeypatch):
    from app.services.issues import issue_session as m

    session = _FakeSession(
        select_row={
            "ai_session_id": None,
            "title": "t",
            "assignee_agent_id": None,
            "created_by_user_id": str(uuid4()),
            "assignee_user_id": None,
            "project_id": None,
            "team_id": None,
        }
    )
    _patch_scopes(monkeypatch, session)

    assert await m.get_or_create_issue_session(1) is None


async def test_raises_when_issue_not_found(monkeypatch):
    from app.services.issues import issue_session as m

    session = _FakeSession(select_row=None)
    _patch_scopes(monkeypatch, session)

    with pytest.raises(RuntimeError, match="not found"):
        await m.get_or_create_issue_session(9999)
