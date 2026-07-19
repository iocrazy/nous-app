"""Unit tests for usage_repository read queries (W3c). DB faked."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.repositories import usage_repository


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
        {"grp": "qwen-max", "prompt_tokens": 200, "completion_tokens": 80,
         "total_tokens": 280, "cost_cents": Decimal("3.00"), "event_count": 4},
        {"grp": None, "prompt_tokens": 100, "completion_tokens": 40,
         "total_tokens": 140, "cost_cents": Decimal("1.20"), "event_count": 3},
    ]
    daily = [
        {"day": "2026-07-17", "grp": "qwen-max", "total_tokens": 140, "cost_cents": Decimal("1.50")},
        {"day": "2026-07-18", "grp": "qwen-max", "total_tokens": 140, "cost_cents": Decimal("1.50")},
    ]

    async def fake_fetch_one(sql, params=None):
        return total

    async def fake_fetch_all(sql, params=None):
        # groups query has GROUP BY grp only; daily has "day"
        return daily if "date_trunc('day'" in sql else groups

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)

    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    out = await usage_repository.summarize(
        team_id="900", frm=now - datetime.timedelta(days=7), to=now, group_by="model"
    )
    assert out["total"]["total_tokens"] == 420
    assert len(out["groups"]) == 2
    assert out["groups"][1]["grp"] is None  # null key preserved
    assert len(out["daily"]) == 2


@pytest.mark.asyncio
async def test_summarize_rejects_unknown_group_falls_back_to_model(monkeypatch):
    seen = {}

    async def fake_fetch_one(sql, params=None):
        seen["one"] = sql
        return {}

    async def fake_fetch_all(sql, params=None):
        seen.setdefault("all", sql)
        return []

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)

    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    await usage_repository.summarize(
        team_id="900", frm=now, to=now + datetime.timedelta(days=1), group_by="bogus"
    )
    # bogus → model key expr
    assert "model" in seen["all"]


@pytest.mark.asyncio
async def test_issue_totals_coerces_and_shapes(monkeypatch):
    captured = {}

    async def fake_fetch_one(sql, params=None):
        captured["params"] = params
        return {
            "prompt_tokens": 50,
            "completion_tokens": 20,
            "total_tokens": 70,
            "cost_cents": Decimal("0.70"),
            "run_count": 2,
        }

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    out = await usage_repository.issue_totals("123456789012345")
    assert captured["params"]["iid"] == 123456789012345
    assert out["total_tokens"] == 70 and out["run_count"] == 2


def test_valid_group_by_set():
    assert usage_repository.VALID_GROUP_BY == frozenset(
        {"agent", "model", "module", "project", "attribution"}
    )
