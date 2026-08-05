"""Unit tests for usage_repository read queries (W3c). DB faked.

ORM (Phase B4): the raw fetch_one/fetch_all calls became
``select(...).where(...)`` through ``app.db.session.read_scope()`` — the
harness patches read_scope and inspects the compiled statement/binds
instead of raw SQL strings, mirroring
tests/test_write_memory_load_recent_messages.py.
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import usage_repository


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: Any) -> None:
        self._rows = rows if isinstance(rows, list) else [rows]

    def mappings(self) -> "_FakeResult":  # noqa: D102
        return self

    def one(self) -> dict:  # noqa: D102
        return self._rows[0]

    def all(self) -> list[dict]:  # noqa: D102
        return list(self._rows)


class _RecordingSession:
    """Records every execute() call (compiled SQL + binds) and returns
    queued fake results in FIFO order."""

    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self._i = 0
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any) -> Any:  # noqa: D102
        self.calls.append(_compile(stmt))
        result = self._results[self._i]
        self._i += 1
        return result


def _install(monkeypatch: pytest.MonkeyPatch, session: _RecordingSession) -> None:
    import app.db.session as db_session

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)


@pytest.mark.asyncio
async def test_summarize_shapes_totals_groups_daily(monkeypatch):
    total = {
        "prompt_tokens": 300,
        "completion_tokens": 120,
        "total_tokens": 420,
        "cached_input_tokens": 10,
        "cost_cents": Decimal("4.20"),
        "event_count": 7,
    }
    groups = [
        {
            "grp": "qwen-max",
            "prompt_tokens": 200,
            "completion_tokens": 80,
            "total_tokens": 280,
            "cost_cents": Decimal("3.00"),
            "event_count": 4,
        },
        {
            "grp": None,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "cost_cents": Decimal("1.20"),
            "event_count": 3,
        },
    ]
    daily = [
        {
            "day": "2026-07-17",
            "grp": "qwen-max",
            "total_tokens": 140,
            "cost_cents": Decimal("1.50"),
        },
        {
            "day": "2026-07-18",
            "grp": "qwen-max",
            "total_tokens": 140,
            "cost_cents": Decimal("1.50"),
        },
    ]

    # Order matters: summarize() issues total, then groups, then daily —
    # all three inside ONE read_scope() session.
    session = _RecordingSession(
        _FakeResult(total), _FakeResult(groups), _FakeResult(daily)
    )
    _install(monkeypatch, session)

    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    out = await usage_repository.summarize(
        team_id="900", frm=now - datetime.timedelta(days=7), to=now, group_by="model"
    )
    assert out["total"]["total_tokens"] == 420
    assert len(out["groups"]) == 2
    assert out["groups"][1]["grp"] is None  # null key preserved
    assert len(out["daily"]) == 2
    assert len(session.calls) == 3

    total_sql, total_binds = session.calls[0]
    assert "public.ai_usage_hourly" in total_sql
    assert total_binds["team_id_1"] == 900

    daily_sql, _daily_binds = session.calls[2]
    assert "date_trunc" in daily_sql
    assert "to_char" in daily_sql


@pytest.mark.asyncio
async def test_summarize_rejects_unknown_group_falls_back_to_model(monkeypatch):
    session = _RecordingSession(_FakeResult({}), _FakeResult([]), _FakeResult([]))
    _install(monkeypatch, session)

    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    await usage_repository.summarize(
        team_id="900", frm=now, to=now + datetime.timedelta(days=1), group_by="bogus"
    )
    # bogus → model key expr (a bare column reference, not a ::text cast)
    groups_sql, _ = session.calls[1]
    assert "public.ai_usage_hourly.model" in groups_sql


@pytest.mark.asyncio
async def test_issue_totals_coerces_and_shapes(monkeypatch):
    session = _RecordingSession(
        _FakeResult(
            {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
                "cost_cents": Decimal("0.70"),
                "run_count": 2,
            }
        )
    )
    _install(monkeypatch, session)

    out = await usage_repository.issue_totals("123456789012345")

    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "public.agent_runs" in sql
    assert binds["issue_id_1"] == 123456789012345
    assert out["total_tokens"] == 70 and out["run_count"] == 2


def test_valid_group_by_set():
    assert usage_repository.VALID_GROUP_BY == frozenset(
        {"agent", "model", "module", "project", "attribution"}
    )
