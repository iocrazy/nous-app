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
    def __init__(
        self,
        *,
        select_row: dict | None,
        rowcount: int = 1,
        meta_row: dict | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._select_row = select_row
        self._rowcount = rowcount
        self._meta_row = meta_row
        self.winner_ai_session_id: Any = None

    async def execute(self, stmt: Any) -> _FakeResult:
        sql, binds = _compile(stmt)
        self.calls.append((sql, binds))
        if sql.startswith("SELECT public.conversation_ai_meta.agent_id"):
            return _FakeResult(mapping=self._meta_row)
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


def _existing_row(sid: str, assignee: Any) -> dict:
    return {
        "ai_session_id": sid,
        "title": "t",
        "assignee_agent_id": assignee,
        "created_by_user_id": str(uuid4()),
        "assignee_user_id": None,
        "project_id": None,
        "team_id": None,
    }


def _spy_agent_repo(monkeypatch: pytest.MonkeyPatch, m: Any, record: Any) -> AsyncMock:
    agent_repo = AsyncMock()
    agent_repo.get_by_id = AsyncMock(return_value=record)
    monkeypatch.setattr(m, "get_agent_repository", lambda: agent_repo)
    return agent_repo


async def test_returns_existing_when_issue_has_session(monkeypatch):
    """Unchanged assignee (meta already bound to it) → zero writes and no
    agent lookup: the common path must stay a pure read."""
    from app.services.issues import issue_session as m

    sid = "315917457926636"
    assignee = uuid4()
    session = _FakeSession(
        select_row=_existing_row(sid, assignee),
        meta_row={"agent_id": assignee, "agent_slug": "writer"},
    )
    _patch_scopes(monkeypatch, session)
    repo = _spy_agent_repo(monkeypatch, m, {"id": str(assignee), "slug": "writer"})

    got = await m.get_or_create_issue_session(409)
    assert got == sid
    # No write should happen — the session already existed and is bound right.
    assert not any("UPDATE" in sql for sql, _ in session.calls)
    repo.get_by_id.assert_not_awaited()


async def test_reassigned_issue_rebinds_the_existing_session(monkeypatch):
    """assignee changed since the session was created → the meta row's
    agent_id/agent_slug swing to the new agent (history kept, no new session)."""
    from app.services.issues import issue_session as m

    sid = "315917457926636"
    old, new = uuid4(), uuid4()
    session = _FakeSession(
        select_row=_existing_row(sid, new),
        meta_row={"agent_id": old, "agent_slug": "script_ai"},
    )
    _patch_scopes(monkeypatch, session)
    repo = _spy_agent_repo(monkeypatch, m, {"id": str(new), "slug": "media-cost-probe"})

    got = await m.get_or_create_issue_session(409)

    assert got == sid  # same session: rebind, not a fork
    repo.get_by_id.assert_awaited_once()
    assert str(repo.get_by_id.await_args.args[0]) == str(new)
    updates = [(sql, b) for sql, b in session.calls if sql.startswith("UPDATE")]
    assert len(updates) == 1
    sql, binds = updates[0]
    assert sql.startswith("UPDATE public.conversation_ai_meta SET")
    assert "public.conversation_ai_meta.conversation_id = " in sql
    assert 315917457926636 in binds.values()  # BIGINT, not str
    assert "media-cost-probe" in binds.values()
    assert new in binds.values()


async def test_meta_without_agent_id_is_rebound_to_the_assignee(monkeypatch):
    """A legacy meta row with agent_id NULL is not 'the same agent' — bind it."""
    from app.services.issues import issue_session as m

    sid = "315917457926636"
    new = uuid4()
    session = _FakeSession(
        select_row=_existing_row(sid, new),
        meta_row={"agent_id": None, "agent_slug": "script_ai"},
    )
    _patch_scopes(monkeypatch, session)
    _spy_agent_repo(monkeypatch, m, {"id": str(new), "slug": "writer"})

    await m.get_or_create_issue_session(409)
    assert any(
        sql.startswith("UPDATE public.conversation_ai_meta") for sql, _ in session.calls
    )


async def test_null_assignee_keeps_the_existing_binding(monkeypatch):
    """assignee cleared (e.g. the agent was deleted → FK SET NULL) → no rebind,
    no lookup; the session is returned as before."""
    from app.services.issues import issue_session as m

    sid = "315917457926636"
    session = _FakeSession(
        select_row=_existing_row(sid, None),
        meta_row={"agent_id": uuid4(), "agent_slug": "script_ai"},
    )
    _patch_scopes(monkeypatch, session)
    repo = _spy_agent_repo(monkeypatch, m, None)

    assert await m.get_or_create_issue_session(409) == sid
    assert not any("UPDATE" in sql for sql, _ in session.calls)
    repo.get_by_id.assert_not_awaited()


async def test_unresolvable_new_assignee_raises_typed_error_and_leaves_meta(
    monkeypatch,
):
    from app.services.issues import issue_session as m

    sid = "315917457926636"
    new = uuid4()
    session = _FakeSession(
        select_row=_existing_row(sid, new),
        meta_row={"agent_id": uuid4(), "agent_slug": "script_ai"},
    )
    _patch_scopes(monkeypatch, session)
    _spy_agent_repo(monkeypatch, m, None)

    with pytest.raises(m.IssueAssigneeNotFound) as exc:
        await m.get_or_create_issue_session(409)
    assert isinstance(exc.value, RuntimeError)  # callers catching RuntimeError still do
    assert exc.value.issue_id == 409
    assert exc.value.agent_id == str(new)
    assert not any("UPDATE" in sql for sql, _ in session.calls)


async def test_missing_meta_row_is_not_rebound(monkeypatch):
    """issues.ai_session_id has no FK; prod has 2 rows pointing at no meta.
    Nothing to swing — return the session as today and let the turn report it."""
    from app.services.issues import issue_session as m

    sid = "315917457926636"
    session = _FakeSession(select_row=_existing_row(sid, uuid4()), meta_row=None)
    _patch_scopes(monkeypatch, session)
    repo = _spy_agent_repo(monkeypatch, m, {"id": "x", "slug": "writer"})

    assert await m.get_or_create_issue_session(409) == sid
    assert not any("UPDATE" in sql for sql, _ in session.calls)
    repo.get_by_id.assert_not_awaited()


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


def test_rebind_helper_is_not_a_dbos_step():
    """get_or_create_issue_session is reached from inside workflow bodies
    (inbox / barrier delivery); the rebind must stay a plain idempotent async
    so it cannot shift any workflow's step order."""
    import inspect

    from app.services.issues import issue_session as m

    for fn in (m.get_or_create_issue_session, m._rebind_to_assignee):
        assert not hasattr(fn, "dbos_function_name")
        assert inspect.unwrap(fn) is fn
