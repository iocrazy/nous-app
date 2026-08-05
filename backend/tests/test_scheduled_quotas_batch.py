"""Unit tests for the batch quota steps (Tier-3 scale fix, mig 287).

Phase B5 Task 2: grant/reclaim moved from ``db_engine.fetch_one`` (raw SQL
string) to a table-valued-function ORM select executed inside
``write_scope()`` — the fixture patches ``app.db.session.write_scope``
instead of the retired ``db_engine.fetch_one``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

import app.db.session as db_session
from app.workflows.scheduled_quotas import (
    grant_daily_free_points_step,
    reclaim_daily_free_points_step,
)


class _FakeResult:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict | None:
        return self._row


class _RecordingSession:
    def __init__(self, row: dict | None) -> None:
        self.calls: list[Any] = []
        self.row = row

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return _FakeResult(self.row)


@pytest.fixture
def write_calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    state: dict = {"row": {"granted": 3, "skipped": 2}, "session": None}

    @asynccontextmanager
    async def fake_write_scope():
        session = _RecordingSession(state["row"])
        state["session"] = session
        yield session

    monkeypatch.setattr(db_session, "write_scope", fake_write_scope)
    return state


def _compiled(stmt: Any) -> str:
    from sqlalchemy.dialects import postgresql

    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.mark.asyncio
async def test_grant_calls_batch_function_once(
    write_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.config.settings.DAILY_FREE_POINTS", 100)
    result = await grant_daily_free_points_step()
    assert result == {"status": "success", "granted": 3, "skipped": 2}

    session = write_calls["session"]
    assert len(session.calls) == 1
    sql = _compiled(session.calls[0])
    assert "grant_daily_free_points_batch" in sql
    assert "100" in sql
    # DATE column — bind a date object, not a datetime.
    today = datetime.now(timezone.utc).date()
    assert str(today) in sql
    assert isinstance(today, date)
    assert not isinstance(today, datetime)


@pytest.mark.asyncio
async def test_grant_skips_when_amount_not_positive(
    write_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.config.settings.DAILY_FREE_POINTS", 0)
    result = await grant_daily_free_points_step()
    assert result["status"] == "skipped"
    assert write_calls["session"] is None  # write_scope never entered


@pytest.mark.asyncio
async def test_grant_raises_on_missing_row(
    write_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.config.settings.DAILY_FREE_POINTS", 100)
    write_calls["row"] = None
    with pytest.raises(RuntimeError, match="no row"):
        await grant_daily_free_points_step()


@pytest.mark.asyncio
async def test_reclaim_calls_batch_function_with_yesterday(
    write_calls: dict,
) -> None:
    write_calls["row"] = {"reclaimed_count": 5, "total_reclaimed": 480}
    result = await reclaim_daily_free_points_step()
    assert result == {
        "status": "success",
        "reclaimed_count": 5,
        "total_reclaimed": 480,
    }
    session = write_calls["session"]
    assert len(session.calls) == 1
    sql = _compiled(session.calls[0])
    assert "reclaim_daily_free_points_batch" in sql
    expected = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    assert str(expected) in sql


@pytest.mark.asyncio
async def test_reclaim_raises_on_missing_row(write_calls: dict) -> None:
    write_calls["row"] = None
    with pytest.raises(RuntimeError, match="no row"):
        await reclaim_daily_free_points_step()
