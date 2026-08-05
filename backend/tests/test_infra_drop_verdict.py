"""Verdict taxonomy P0 — infra-drop is 'lost' (retryable), NOT 'failed'.

A worker dying (heartbeat stale → E3) or redeploying before a workflow ran
(version-orphan → E2) is infra dropping the work, not the workflow faulting.
The terminal task_tracking status must be 'lost' (UI shows interrupted +
Retry), never 'failed' ('failed' is reserved for E1 = a real DBOS ERROR with
the decoded reason, written by reap_stuck_pending_tasks_step Pass A).

These lock the relabel so a future edit can't silently regress infra-drop back
to 'failed'. The writers do an ORM UPDATE against ``app.db.session.write_scope``
(Phase B3 rewrite of the raw ``db_engine.execute`` calls); we capture the
compiled statement + bind params via a fake session rather than hitting a
database. ``_cancel_dbos_zombie`` still does one RAW write first (CANCELLING
the row in ``dbos.workflow_status`` — a structural exception, never ORM'd),
so that call is captured via a fake ``app.db.engine.execute`` alongside the
fake ORM session for its task_tracking reconcile write.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.workflows.workflow_health_sweeper as sweeper


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _FakeSession:
    """Captures every (compiled_sql, binds) pair handed to execute()."""

    def __init__(self, rowcount: int = 1) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rowcount = rowcount

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self._rowcount)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_write_scope(monkeypatch, session: _FakeSession) -> None:
    import app.db.session as dbs

    monkeypatch.setattr(dbs, "write_scope", lambda: _ScopeCM(session))


def _patch_engine_execute(monkeypatch, captured: list, *, execute_returns: int = 1):
    """Patch the raw ``app.db.engine.execute`` the dbos.workflow_status CANCEL
    write still goes through (structural exception — never ORM'd)."""

    async def fake_execute(sql, params=None):
        captured.append(sql)
        return execute_returns

    monkeypatch.setattr("app.db.engine.execute", fake_execute, raising=False)


@pytest.mark.asyncio
async def test_mark_lost_writes_status_lost_not_failed(monkeypatch):
    session = _FakeSession()
    _patch_write_scope(monkeypatch, session)

    await sweeper._mark_lost(
        {"dbos_workflow_id": "wf-1", "task_type": "download", "title": "x"}
    )

    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "task_tracking" in sql
    assert binds["status"] == "lost"
    assert binds["phase"] == "lost"
    assert binds["error_code"] == "worker_lost"
    assert binds["dbos_workflow_id_1"] == "wf-1"


@pytest.mark.asyncio
async def test_cancel_dbos_zombie_reconciles_to_lost_not_failed(monkeypatch):
    dbos_captured: list[str] = []
    # First write = the raw dbos.workflow_status CANCEL (returns 1 → proceed).
    _patch_engine_execute(monkeypatch, dbos_captured)
    session = _FakeSession()
    _patch_write_scope(monkeypatch, session)

    ok = await sweeper._cancel_dbos_zombie("wf-2")
    assert ok is True

    assert dbos_captured and "dbos.workflow_status" in dbos_captured[0]
    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "task_tracking" in sql
    assert binds["status"] == "lost"
    assert binds["phase"] == "lost"
    assert binds["dbos_workflow_id_1"] == "wf-2"
    assert binds["status_1"] == "completed"  # the guard: AND status <> 'completed'


@pytest.mark.asyncio
async def test_cancel_dbos_zombie_noop_when_already_terminal(monkeypatch):
    """If the engine CANCEL touches 0 rows (raced to terminal), no reconcile
    write happens and the call reports it didn't cancel."""
    dbos_captured: list[str] = []
    _patch_engine_execute(monkeypatch, dbos_captured, execute_returns=0)
    session = _FakeSession()
    _patch_write_scope(monkeypatch, session)

    ok = await sweeper._cancel_dbos_zombie("wf-3")
    assert ok is False
    assert not session.calls  # no task_tracking reconcile write attempted
