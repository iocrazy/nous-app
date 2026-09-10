"""``enqueue(dedupe_key=…)`` — one delivery per key, so a replayed workflow
body does not queue the same wake-up twice.

The key is stored inside ``content`` (no column, no migration): the lookup is
a jsonb ``->>`` on the target's live rows. "Live" is pending OR claimed — an
EXPIRED row was never consumed, so re-queuing it is the right answer.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import agent_run_inbox_repository as repo

pytestmark = pytest.mark.unit


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).lower()


class _Row:
    """Stands in for an AgentRunInbox ORM instance (``_row`` reads columns)."""

    def __init__(self, row_id: int) -> None:
        from app.models import AgentRunInbox

        self.__table__ = AgentRunInbox.__table__
        for column in AgentRunInbox.__table__.columns:
            setattr(self, column.key, None)
        self.id = row_id
        self.target_kind = "issue"
        self.target_id = 5
        self.kind = "steer"
        self.content = {"text": "wake", "dedupe_key": "sched:s1:t0"}
        self.created_at = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalar_one(self):
        return self._rows[0]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, results: list[_Result]) -> None:
        self.statements: list = []
        self._results = list(results)

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _Result([])


def _patch(monkeypatch, session: _Session) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(repo, "write_scope", _scope)


def test_the_lookup_matches_the_key_on_the_target_and_ignores_expired_rows():
    sql = _sql(repo.dedupe_lookup_stmt("issue", 5, "sched:s1:t0"))
    assert "from public.agent_run_inbox" in sql
    assert "target_kind" in sql and "target_id" in sql
    assert "content ->> " in sql
    # claimed rows still count as delivered; expired ones do not.
    assert "expired_at is null" in sql
    assert "claimed_at is null" not in sql


async def test_a_first_delivery_inserts_and_stamps_the_key(monkeypatch):
    session = _Session([_Result([]), _Result([_Row(1)])])
    _patch(monkeypatch, session)

    out = await repo.AgentRunInboxRepository().enqueue(
        target_kind="issue",
        target_id=5,
        user_id="u1",
        kind="steer",
        content={"text": "wake"},
        dedupe_key="sched:s1:t0",
    )

    assert out["id"] == 1
    insert_sql = _sql(session.statements[1])
    assert "insert into public.agent_run_inbox" in insert_sql
    params = session.statements[1].compile().params
    assert params["content"] == {"text": "wake", "dedupe_key": "sched:s1:t0"}


async def test_a_replayed_delivery_returns_the_existing_row_without_inserting(
    monkeypatch,
):
    session = _Session([_Result([_Row(42)])])
    _patch(monkeypatch, session)

    out = await repo.AgentRunInboxRepository().enqueue(
        target_kind="issue",
        target_id=5,
        user_id="u1",
        kind="steer",
        content={"text": "wake"},
        dedupe_key="sched:s1:t0",
    )

    assert out["id"] == 42
    assert len(session.statements) == 1, "the duplicate must not be inserted"


async def test_without_a_key_nothing_is_looked_up(monkeypatch):
    """Every other caller keeps today's one-statement behaviour."""
    session = _Session([_Result([_Row(7)])])
    _patch(monkeypatch, session)

    out = await repo.AgentRunInboxRepository().enqueue(
        target_kind="issue",
        target_id=5,
        user_id="u1",
        kind="steer",
        content={"text": "wake"},
    )

    assert out["id"] == 7
    assert len(session.statements) == 1
    assert "insert into" in _sql(session.statements[0])
