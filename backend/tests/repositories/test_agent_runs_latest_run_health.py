"""latest_run_health_by_agent — the query behind the gallery's fault badge.

The badge used to answer "did anything break in the last 7 days?" while
users read it as "is this agent broken right now". Two rules fix that, and
both are pinned here:

    - the DISTINCT ON winner is the agent's LAST finished run, chosen with no
      liveness filter in the WHERE, so a later success can clear the badge;
    - only an unhealthy winner (stuck / dead) comes back.

DB faked — the harness patches read_scope and inspects the compiled
statement, mirroring tests/repositories/test_usage_repository.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.agent_runs_repository import AgentRunsRepository

AGENT_A = uuid4()
SINCE = datetime.now(timezone.utc) - timedelta(days=7)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:  # noqa: D102
        return self._rows


class _RecordingSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.sql: str = ""

    async def execute(self, stmt: Any) -> Any:  # noqa: D102
        self.sql = str(stmt.compile(dialect=postgresql.dialect()))
        return _FakeResult(self._rows)


def _install(monkeypatch: pytest.MonkeyPatch, session: _RecordingSession) -> None:
    import app.repositories.agent_runs_repository as mod

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", fake_read_scope)


def _row(liveness_state: str, error_code: str | None, error_message: str | None):
    return SimpleNamespace(
        agent_id=AGENT_A,
        liveness_state=liveness_state,
        error_code=error_code,
        error_message=error_message,
    )


@pytest.mark.asyncio
async def test_dead_latest_run_is_reported(monkeypatch):
    session = _RecordingSession([_row("dead", "liveness_dead", "boom")])
    _install(monkeypatch, session)

    out = await AgentRunsRepository().latest_run_health_by_agent([AGENT_A], SINCE)

    assert out[str(AGENT_A)] == {
        "liveness_state": "dead",
        "error_code": "liveness_dead",
        "error_message": "boom",
    }


@pytest.mark.asyncio
async def test_a_healthy_latest_run_clears_the_agent(monkeypatch):
    """The regression this method was rewritten for.

    The DISTINCT ON winner is the newest finished run. When that run wound up
    normally the agent must drop out of the map entirely — under the old
    "select dead rows in the window" shape a single bad run kept the badge
    lit for a week no matter how many runs succeeded afterwards.
    """
    session = _RecordingSession([_row("finished", None, None)])
    _install(monkeypatch, session)

    out = await AgentRunsRepository().latest_run_health_by_agent([AGENT_A], SINCE)

    assert out == {}


@pytest.mark.asyncio
async def test_a_cancelled_latest_run_clears_the_agent(monkeypatch):
    """Same rule, user-initiated: they stopped the run, nothing is broken."""
    session = _RecordingSession([_row("cancelled", None, None)])
    _install(monkeypatch, session)

    out = await AgentRunsRepository().latest_run_health_by_agent([AGENT_A], SINCE)

    assert out == {}


@pytest.mark.asyncio
async def test_query_ranks_by_recency_without_prefiltering_liveness(monkeypatch):
    """Guards the mechanism, not just the outcome.

    If liveness_state moved back into the WHERE clause, every assertion above
    would still pass (the fake rows are handed over directly) while the real
    query silently went back to "newest DEAD run" and the badge stopped
    clearing. So: pin that the statement picks one row per agent by descending
    start time, over every non-running row.
    """
    session = _RecordingSession([])
    _install(monkeypatch, session)

    await AgentRunsRepository().latest_run_health_by_agent([AGENT_A], SINCE)

    sql = " ".join(session.sql.split())
    assert "DISTINCT ON (public.agent_runs.agent_id)" in sql
    assert (
        "ORDER BY public.agent_runs.agent_id, public.agent_runs.started_at DESC" in sql
    )
    assert "public.agent_runs.status !=" in sql
    assert "liveness_state" not in sql.split("ORDER BY")[0].split("WHERE")[-1]


@pytest.mark.asyncio
async def test_empty_agent_list_does_not_query(monkeypatch):
    session = _RecordingSession([_row("dead", "liveness_dead", "boom")])
    _install(monkeypatch, session)

    assert await AgentRunsRepository().latest_run_health_by_agent([], SINCE) == {}
    assert session.sql == ""
