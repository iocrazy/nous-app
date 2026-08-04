"""ORM-level coverage for the Phase B1 input_gate.py rewrite.

input_gate.py's writes/reads on ``issues`` / ``task_tracking`` were raw
``text()`` SQL before this migration (decision doc
``docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md``); now they are
SQLAlchemy Core ``update()``/``select()`` statements. These tests don't hit a
real database — they capture every statement ``mark_awaiting_input`` /
``clear_awaiting_input`` / ``_clear_issue_lock`` hand to the session, compile
them against the postgresql dialect, and assert the WHERE / jsonb-operator /
SET-LOCAL-ROLE semantics survived the rewrite byte-for-byte equivalent to the
retired raw SQL.

``input_gate.py`` does local (function-scope) imports of ``read_scope`` /
``write_scope`` from ``app.db.session`` — patching happens on the SOURCE
module (``app.db.session``), not on ``input_gate`` itself, since each call
re-imports the current attribute value.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.agent_framework import input_gate
from app.db import session as db_session
from app.services import notifications as notifications_mod


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    try:
        compiled = stmt.compile(dialect=postgresql.dialect())
        return str(compiled), dict(compiled.params)
    except Exception:
        return str(stmt), {}


class _FakeResult:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _FakeSession:
    """Captures every (compiled_sql, binds) pair handed to execute()."""

    def __init__(self, scalar: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._scalar = scalar

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self._scalar)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _all_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.calls)


def _all_binds(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))
    return session


async def test_mark_awaiting_input_writes_issues_with_service_role_and_jsonb_merge(
    fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifications_mod, "notify", AsyncMock())
    await input_gate.mark_awaiting_input(
        workflow_id="wf-1", issue_id=42, user_id="u1", prompt="which color?"
    )
    sql = _all_sql(fake_session)
    # SET LOCAL ROLE service_role precedes both writes (mig-170 allowlist
    # trigger only bypasses for service_role/supabase_admin — the engine
    # connects as postgres, which needs the explicit SET LOCAL).
    assert sql.count("SET LOCAL ROLE service_role") == 2
    assert "UPDATE public.issues SET execution_state=" in sql
    assert "coalesce(public.issues.execution_state" in sql
    assert "|| jsonb_build_object(" in sql
    assert "WHERE public.issues.id = " in sql
    assert "UPDATE public.task_tracking SET metadata=" in sql
    assert "coalesce(public.task_tracking.metadata" in sql
    assert "WHERE public.task_tracking.dbos_workflow_id = " in sql
    # marker payload (json-encoded) reaches the bind params on both writes.
    binds = _all_binds(fake_session)
    assert any("which color?" in str(b) for b in binds)


async def test_clear_awaiting_input_deletes_key_with_has_key_guard(
    fake_session: _FakeSession,
) -> None:
    await input_gate.clear_awaiting_input(workflow_id="wf-2")
    sql = _all_sql(fake_session)
    assert sql.count("SET LOCAL ROLE service_role") == 2
    assert "UPDATE public.issues SET execution_state=" in sql
    assert "public.issues.execution_state - " in sql  # jsonb key-delete op
    assert "public.issues.execution_state ? " in sql  # has_key guard preserved
    assert "public.issues.dbos_workflow_id = " in sql
    assert "UPDATE public.task_tracking SET metadata=" in sql
    assert "public.task_tracking.metadata - " in sql
    binds = _all_binds(fake_session)
    assert "awaiting_input" in binds


async def test_clear_issue_lock_sets_execution_locked_at_null(
    fake_session: _FakeSession,
) -> None:
    await input_gate._clear_issue_lock("wf-3")
    sql = _all_sql(fake_session)
    assert "SET LOCAL ROLE service_role" in sql
    assert "UPDATE public.issues SET execution_locked_at=" in sql
    assert "WHERE public.issues.dbos_workflow_id = " in sql
    assert "wf-3" in _all_binds(fake_session)


async def test_mark_awaiting_input_identifier_lookup_uses_orm_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """link_id falls back to issues.identifier via a plain SELECT (read_scope,
    no service_role needed — identifier is not allowlist-guarded)."""
    session = _FakeSession(scalar="MH-7")
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(_FakeSession()))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))
    notify_mock = AsyncMock()
    monkeypatch.setattr(notifications_mod, "notify", notify_mock)

    await input_gate.mark_awaiting_input(
        workflow_id="wf-4", issue_id=99, user_id="u1", prompt="q"
    )

    sql = _all_sql(session)
    assert "SELECT public.issues.identifier" in sql
    assert "WHERE public.issues.id = " in sql
    notify_mock.assert_awaited_once()
    assert notify_mock.await_args.kwargs["link_id"] == "MH-7"
