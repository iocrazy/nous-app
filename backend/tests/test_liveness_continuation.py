"""Spec-2 hygiene: wire the dead liveness continuation_attempt scaffolding.

_recover_from_stuck must atomically flip stuck→running AND bump
continuation_attempt, so a run that keeps flapping stuck→running burns through
MAX_CONTINUATIONS and is finally judged dead (closes the flap-forever hole).

ORM (Phase B4): _recover_from_stuck now writes via app.db.session.write_scope()
+ SQLAlchemy Core update(AgentRuns) instead of a raw db_engine.execute() call —
the harness patches write_scope and inspects the compiled statement.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows import liveness_scanner as ls


class _FakeResult:
    rowcount = 1


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.calls.append((str(compiled), dict(compiled.params)))
        return _FakeResult()


@pytest.mark.asyncio
async def test_recover_from_stuck_bumps_continuation_and_cas_guards(monkeypatch):
    session = _RecordingSession()

    @asynccontextmanager
    async def fake_write_scope():
        yield session

    monkeypatch.setattr(db_session, "write_scope", fake_write_scope)

    await ls._recover_from_stuck(42)

    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "agent_runs" in sql
    # increments the counter
    assert "continuation_attempt=(public.agent_runs.continuation_attempt +" in sql
    # flips to running
    assert binds["liveness_state"] == "running"
    # CAS-guards on the stuck precondition (idempotent under concurrent scans)
    assert binds["liveness_state_1"] == "stuck"
    assert binds["id_1"] == 42


def test_should_auto_continue_is_gone():
    # Dead helper removed; importing it must fail (no lingering dead export).
    with pytest.raises(ImportError):
        from app.agent_framework.output_budget import (  # noqa: F401
            should_auto_continue,
        )
