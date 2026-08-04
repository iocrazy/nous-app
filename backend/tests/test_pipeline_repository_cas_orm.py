"""ORM-level coverage for pipeline_repository.py's CAS run-transition writers
(Phase B1 rewrite of raw ``UPDATE ... RETURNING *`` SQL).

``advance_run_step`` / ``complete_run`` / ``halt_run`` / ``cancel_run`` are the
idempotency substrate for content-relay pipelines (W2b) — two racing observers
of the same terminal edge must land on the SAME single-row UPDATE, with the
loser getting 0 rows back. These tests don't hit a real database — they
capture the compiled ``update(IssuePipelineRuns)...returning(...)`` statement
and assert the WHERE clause (the actual CAS guard: ``status = 'running'`` AND,
where applicable, ``current_step = :from_step``) survived the raw-SQL → ORM
rewrite unchanged, plus the win/lose (row returned vs. None) behavior.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import pipeline_repository as mod
from app.repositories.pipeline_repository import PipelineRepository


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _Obj:
    """Stand-in ORM entity returned by ``.scalar_one_or_none()`` on a
    ``RETURNING`` result — the CAS "winner" row."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def _run_obj(**overrides: Any) -> _Obj:
    base = dict(
        id=1,
        pipeline_id=2,
        parent_issue_id=3,
        current_step=2,
        status="running",
        halted_reason=None,
        started_by_user_id=None,
        created_at=datetime.datetime(2026, 1, 1),
        updated_at=datetime.datetime(2026, 1, 1),
        completed_at=None,
    )
    base.update(overrides)
    return _Obj(**base)


class _FakeResult:
    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def scalar_one_or_none(self) -> Any:
        return self._obj


class _FakeSession:
    def __init__(self, obj: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._obj = obj

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self._obj)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch(monkeypatch: pytest.MonkeyPatch, obj: Any) -> _FakeSession:
    session = _FakeSession(obj)
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> PipelineRepository:
    return PipelineRepository()


async def test_advance_run_step_cas_where_clause(
    repo: PipelineRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _patch(monkeypatch, _run_obj(current_step=2))
    row = await repo.advance_run_step(1, from_step=1, to_step=2)
    assert row is not None and row["current_step"] == 2
    sql, binds = session.calls[0]
    assert "UPDATE public.issue_pipeline_runs SET current_step=" in sql
    assert "public.issue_pipeline_runs.status = " in sql
    assert "public.issue_pipeline_runs.current_step = " in sql
    assert "RETURNING" in sql
    assert binds["status_1"] == "running"
    assert binds["current_step_1"] == 1  # from_step is the CAS guard
    assert binds["current_step"] == 2  # to_step is the SET value


async def test_advance_run_step_cas_loser_returns_none(
    repo: PipelineRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 rows (another observer already advanced) → scalar_one_or_none is
    None → the repo must surface None, not raise or fabricate a row."""
    _patch(monkeypatch, None)
    row = await repo.advance_run_step(1, from_step=1, to_step=2)
    assert row is None


async def test_complete_run_cas_where_clause(
    repo: PipelineRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _patch(monkeypatch, _run_obj(status="completed"))
    row = await repo.complete_run(1, from_step=3)
    assert row is not None and row["status"] == "completed"
    sql, binds = session.calls[0]
    assert "UPDATE public.issue_pipeline_runs SET status=" in sql
    assert "completed_at=" in sql and "updated_at=" in sql
    assert "public.issue_pipeline_runs.status = " in sql
    assert "public.issue_pipeline_runs.current_step = " in sql
    assert binds["status_1"] == "running"  # CAS guard, not the new value
    assert binds["status"] == "completed"
    assert binds["current_step_1"] == 3


async def test_halt_run_cas_has_no_step_guard(
    repo: PipelineRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """halt/cancel CAS only on status='running' — no current_step condition
    (a halt can fire from any step, unlike advance/complete)."""
    session = _patch(monkeypatch, _run_obj(status="halted", halted_reason="x"))
    row = await repo.halt_run(1, reason="budget exceeded")
    assert row is not None and row["halted_reason"] == "x"
    sql, binds = session.calls[0]
    where_clause = sql.split(" WHERE ", 1)[1].split(" RETURNING")[0]
    assert "UPDATE public.issue_pipeline_runs SET status=" in sql
    assert "halted_reason=" in sql
    assert "current_step" not in where_clause
    assert binds["status_1"] == "running"
    assert binds["status"] == "halted"
    assert binds["halted_reason"] == "budget exceeded"


async def test_cancel_run_cas_where_clause(
    repo: PipelineRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _patch(monkeypatch, _run_obj(status="cancelled"))
    row = await repo.cancel_run(1)
    assert row is not None and row["status"] == "cancelled"
    sql, binds = session.calls[0]
    where_clause = sql.split(" WHERE ", 1)[1].split(" RETURNING")[0]
    assert "UPDATE public.issue_pipeline_runs SET status=" in sql
    assert "current_step" not in where_clause
    assert binds["status_1"] == "running"
    assert binds["status"] == "cancelled"


async def test_halt_run_already_terminal_returns_none(
    repo: PipelineRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, None)
    assert await repo.halt_run(1, reason="x") is None
