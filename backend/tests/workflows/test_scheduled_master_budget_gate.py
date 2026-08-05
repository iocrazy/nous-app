"""W3c budget breaker in the autopilot scheduler.

Over budget → _fire_agent_routine skips the fire (returns None), bumps
skipped_count, and never creates/dispatches an issue.

ORM (Phase B4): the owner→personal-team lookup and the skipped_count bump
moved from raw db_engine.fetch_val/execute calls to SQLAlchemy Core through
app.db.session.read_scope()/write_scope() — the harness patches those scopes
with a recording session (mirrors test_orm_b3_task1_compile_coverage.py)
instead of the raw engine helpers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows import scheduled_master as sm


def _row():
    return {
        "id": 900555,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "name": "Daily digest",
        "payload": {
            "agent_slug": "script_ai",
            "prompt_md": "Summarize today",
            "delivery_policy": "skip_if_active",
        },
    }


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows if rows is not None else []
        self._scalar = scalar

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _RecordingSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.calls: list[Any] = []
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._results.pop(0) if self._results else self._default


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    session = _RecordingSession(results)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_scope)
    monkeypatch.setattr(db_session, "write_scope", fake_scope)
    return session


@pytest.mark.asyncio
async def test_over_budget_skips_and_bumps_skipped_count(monkeypatch):
    # budget_team_id lookup (scalar) then the skipped_count UPDATE.
    session = _patch_scopes(monkeypatch, [_FakeResult(scalar=900123)])

    async def over_budget(team_id):
        assert team_id == 900123
        return True

    # atomic_create must NOT be reached when over budget.
    called = {"created": False}

    class _IssueRepo:
        async def atomic_create(self, body):
            called["created"] = True
            return {"id": 1}

    monkeypatch.setattr("app.services.ai_usage.is_team_over_budget", over_budget)
    import app.repositories.issue_repository as ir

    monkeypatch.setattr(ir, "issue_repository", _IssueRepo())

    result = await sm._fire_agent_routine(_row())

    assert result is None  # skipped, no dispatch order
    assert called["created"] is False  # no paid issue created
    assert len(session.calls) == 2
    update_sql, update_params = _compile(session.calls[1])
    assert "skipped_count=(public.user_schedules.skipped_count +" in update_sql
    assert update_params["id_1"] == 900555


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_under_budget_passes_gate(monkeypatch):
    """Not over budget → the gate is a no-op; execution proceeds past it (proven
    by reaching the agent lookup, which we stub to a not-found RuntimeError)."""
    # First read_scope call is the budget_team_id lookup (scalar); second is
    # the delivery-gate/agent lookup (mappings().first() → None → not found).
    _patch_scopes(monkeypatch, [_FakeResult(scalar=900123), _FakeResult(rows=[])])

    async def under_budget(team_id):
        return False

    monkeypatch.setattr("app.services.ai_usage.is_team_over_budget", under_budget)

    with pytest.raises(RuntimeError, match="not found"):
        await sm._fire_agent_routine(_row())
