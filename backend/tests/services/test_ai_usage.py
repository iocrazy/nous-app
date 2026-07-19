"""Unit tests for the W3c AI usage recording + budget breaker service.

No DB — app.db.engine calls are monkeypatched so we assert the SQL params
(bucket truncation, id coercion, attribution defaulting) and the fire-and-forget
swallow behaviour.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.services import ai_usage


def test_attribution_from_origin_kind():
    assert ai_usage.attribution_from_origin_kind("routine") == "rule_owner"
    assert ai_usage.attribution_from_origin_kind("pipeline") == "rule_owner"
    assert ai_usage.attribution_from_origin_kind("manual") == "direct_human"
    assert ai_usage.attribution_from_origin_kind("chat_delegate") == "direct_human"
    assert ai_usage.attribution_from_origin_kind(None) == "direct_human"


@pytest.mark.asyncio
async def test_record_usage_upserts_rollup_with_truncated_bucket(monkeypatch):
    captured: dict = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    monkeypatch.setattr("app.db.engine.execute", fake_execute)

    occurred = datetime.datetime(2026, 7, 18, 13, 47, 22, tzinfo=datetime.timezone.utc)
    await ai_usage.record_usage(
        module="issue_dispatch",
        attribution="rule_owner",
        prompt_tokens=100,
        completion_tokens=40,
        cached_input_tokens=5,
        occurred_at=occurred,
        team_id="900123456789",  # str → must coerce to int
        project_id=None,
        agent_id="11111111-1111-1111-1111-111111111111",
        model="qwen-max",
        cost_cents=Decimal("1.2345"),
    )

    p = captured["params"]
    # bucket truncated to the hour
    assert p["bucket_hour"] == occurred.replace(minute=0, second=0, microsecond=0)
    assert p["team_id"] == 900123456789 and isinstance(p["team_id"], int)
    assert p["project_id"] is None
    assert p["agent_id"] == "11111111-1111-1111-1111-111111111111"
    assert p["module"] == "issue_dispatch"
    assert p["attribution"] == "rule_owner"
    assert p["prompt_tokens"] == 100 and p["completion_tokens"] == 40
    assert p["cached_input_tokens"] == 5
    assert p["cost_cents"] == Decimal("1.2345")
    assert "ON CONFLICT ON CONSTRAINT ai_usage_hourly_dims_uq" in captured["sql"]


@pytest.mark.asyncio
async def test_record_usage_defaults_and_null_cost(monkeypatch):
    captured: dict = {}

    async def fake_execute(sql, params=None):
        captured["params"] = params
        return 1

    monkeypatch.setattr("app.db.engine.execute", fake_execute)

    await ai_usage.record_usage(
        module="chat",
        attribution=None,  # → direct_human
        prompt_tokens=10,
        completion_tokens=0,
        cost_cents=None,  # unpriced → 0 in the rollup
    )
    p = captured["params"]
    assert p["attribution"] == "direct_human"
    assert p["cost_cents"] == Decimal(0)


@pytest.mark.asyncio
async def test_record_usage_never_raises(monkeypatch):
    async def boom(sql, params=None):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.engine.execute", boom)
    # Must swallow — a telemetry failure cannot break the AI call.
    await ai_usage.record_usage(
        module="chat", attribution="direct_human", prompt_tokens=1, completion_tokens=1
    )


@pytest.mark.asyncio
async def test_is_team_over_budget_branches(monkeypatch):
    # No team → False
    assert await ai_usage.is_team_over_budget(None) is False

    async def budget_row(_tid):
        return {"monthly_budget_cents": Decimal("500")}

    async def no_budget_row(_tid):
        return None

    async def null_ceiling_row(_tid):
        return {"monthly_budget_cents": None}

    async def spend_600(_tid):
        return Decimal("600")

    async def spend_100(_tid):
        return Decimal("100")

    # over budget
    monkeypatch.setattr(ai_usage, "get_team_budget", budget_row)
    monkeypatch.setattr(ai_usage, "get_team_month_spend_cents", spend_600)
    assert await ai_usage.is_team_over_budget("900") is True

    # under budget
    monkeypatch.setattr(ai_usage, "get_team_month_spend_cents", spend_100)
    assert await ai_usage.is_team_over_budget("900") is False

    # no budget row → unlimited
    monkeypatch.setattr(ai_usage, "get_team_budget", no_budget_row)
    assert await ai_usage.is_team_over_budget("900") is False

    # null ceiling → unlimited
    monkeypatch.setattr(ai_usage, "get_team_budget", null_ceiling_row)
    monkeypatch.setattr(ai_usage, "get_team_month_spend_cents", spend_600)
    assert await ai_usage.is_team_over_budget("900") is False


@pytest.mark.asyncio
async def test_is_team_over_budget_fails_open(monkeypatch):
    async def boom(_tid):
        raise RuntimeError("db down")

    monkeypatch.setattr(ai_usage, "get_team_budget", boom)
    # A lookup failure must not wedge dispatch → fail open (False).
    assert await ai_usage.is_team_over_budget("900") is False
