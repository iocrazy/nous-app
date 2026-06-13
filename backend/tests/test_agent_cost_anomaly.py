"""Tests for the agent cost anomaly detector (Phase 4.5 — 4.5-7)."""

from __future__ import annotations

import pytest

from app.workflows.agent_cost_anomaly import (
    MIN_BASELINE_HOURS,
    MIN_HOUR_COST_CENTS,
    Z_THRESHOLD,
    detect_agent_cost_anomalies_step,
)


@pytest.fixture
def engine_rec(monkeypatch: pytest.MonkeyPatch) -> dict:
    rec: dict = {
        "fetch_all": [],
        "execute": [],
        "returning": [],
        "findings": [],
        "rule_id": 42,
    }

    async def fake_fetch_all(sql: str, params=None):
        rec["fetch_all"].append({"sql": sql, "params": params})
        return rec["findings"]

    async def fake_fetch_val(sql: str, params=None):
        return rec["rule_id"]

    async def fake_execute(sql: str, params=None):
        rec["execute"].append({"sql": sql, "params": params})
        return 1

    async def fake_execute_returning_val(sql: str, params=None):
        rec["returning"].append({"sql": sql, "params": params})
        return 99

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr("app.db.engine.fetch_val", fake_fetch_val)
    monkeypatch.setattr("app.db.engine.execute", fake_execute)
    monkeypatch.setattr(
        "app.db.engine.execute_returning_val", fake_execute_returning_val
    )
    return rec


def _finding(**over) -> dict:
    base = {
        "agent_id": "a-1",
        "agent_slug": "script_ai",
        "hour_cost_cents": 800.0,
        "baseline_mean": 100.0,
        "baseline_sd": 50.0,
        "baseline_hours": 100,
        "zscore": 14.0,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_no_findings_is_quiet(engine_rec: dict) -> None:
    result = await detect_agent_cost_anomalies_step()
    assert result == {"status": "success", "anomalies": 0}
    assert engine_rec["execute"] == []  # no history rows, no rule churn
    params = engine_rec["fetch_all"][0]["params"]
    assert params == {
        "min_hours": MIN_BASELINE_HOURS,
        "z": Z_THRESHOLD,
        "min_cost": MIN_HOUR_COST_CENTS,
    }


@pytest.mark.asyncio
async def test_findings_land_in_alert_history(engine_rec: dict) -> None:
    engine_rec["findings"] = [_finding(), _finding(agent_slug="storyboard")]
    result = await detect_agent_cost_anomalies_step()
    assert result["anomalies"] == 2
    inserts = [c for c in engine_rec["execute"] if "alert_history" in c["sql"]]
    assert len(inserts) == 2
    first = inserts[0]["params"]
    assert first["rid"] == 42  # anchored to the existing system rule
    assert first["z"] == 14.0
    assert "script_ai" in first["msg"]
    assert "800" in first["msg"]


@pytest.mark.asyncio
async def test_anchor_rule_created_inactive_when_missing(
    engine_rec: dict,
) -> None:
    engine_rec["rule_id"] = None  # lookup misses → create path
    engine_rec["findings"] = [_finding()]
    await detect_agent_cost_anomalies_step()
    assert len(engine_rec["returning"]) == 1
    create_sql = engine_rec["returning"][0]["sql"]
    # MUST be inactive — the admin /check loop walks active rules only,
    # and this rule has no evaluable metric there.
    assert "FALSE" in create_sql
    insert = [c for c in engine_rec["execute"] if "alert_history" in c["sql"]][0]
    assert insert["params"]["rid"] == 99
