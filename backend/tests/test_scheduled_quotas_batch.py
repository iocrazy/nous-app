"""Unit tests for the batch quota steps (Tier-3 scale fix, mig 287)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.workflows.scheduled_quotas import (
    grant_daily_free_points_step,
    reclaim_daily_free_points_step,
)


@pytest.fixture
def fetch_calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"sql": [], "params": [], "result": {"granted": 3, "skipped": 2}}

    async def fake_fetch_one(sql: str, params=None):
        calls["sql"].append(sql)
        calls["params"].append(params)
        return calls["result"]

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    return calls


@pytest.mark.asyncio
async def test_grant_calls_batch_function_once(
    fetch_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.config.settings.DAILY_FREE_POINTS", 100)
    result = await grant_daily_free_points_step()
    assert result == {"status": "success", "granted": 3, "skipped": 2}
    assert len(fetch_calls["sql"]) == 1
    assert "grant_daily_free_points_batch" in fetch_calls["sql"][0]
    params = fetch_calls["params"][0]
    assert params["amt"] == 100
    # DATE column — bind a date object, not a datetime.
    assert isinstance(params["today"], date)
    assert not isinstance(params["today"], datetime)


@pytest.mark.asyncio
async def test_grant_skips_when_amount_not_positive(
    fetch_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.config.settings.DAILY_FREE_POINTS", 0)
    result = await grant_daily_free_points_step()
    assert result["status"] == "skipped"
    assert fetch_calls["sql"] == []


@pytest.mark.asyncio
async def test_grant_raises_on_missing_row(
    fetch_calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.config.settings.DAILY_FREE_POINTS", 100)
    fetch_calls["result"] = None
    with pytest.raises(RuntimeError, match="no row"):
        await grant_daily_free_points_step()


@pytest.mark.asyncio
async def test_reclaim_calls_batch_function_with_yesterday(
    fetch_calls: dict,
) -> None:
    fetch_calls["result"] = {"reclaimed_count": 5, "total_reclaimed": 480}
    result = await reclaim_daily_free_points_step()
    assert result == {
        "status": "success",
        "reclaimed_count": 5,
        "total_reclaimed": 480,
    }
    assert len(fetch_calls["sql"]) == 1
    assert "reclaim_daily_free_points_batch" in fetch_calls["sql"][0]
    expected = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    assert fetch_calls["params"][0]["yesterday"] == expected


@pytest.mark.asyncio
async def test_reclaim_raises_on_missing_row(fetch_calls: dict) -> None:
    fetch_calls["result"] = None
    with pytest.raises(RuntimeError, match="no row"):
        await reclaim_daily_free_points_step()
