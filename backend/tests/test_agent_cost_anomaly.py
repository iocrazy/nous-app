"""Tests for the agent cost anomaly detector (Phase 4.5 — 4.5-7).

ORM (Phase B4): the CTE scan / anchor-rule get-or-create / alert_history
insert all moved from raw ``db_engine.fetch_all``/``execute``/
``execute_returning_val`` calls to SQLAlchemy Core through
``app.db.session.read_scope()``/``write_scope()``. The harness patches those
scopes with a shared recording session (mirrors
tests/test_orm_b3_task1_compile_coverage.py's technique) instead of the raw
engine helpers.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows.agent_cost_anomaly import (
    MIN_BASELINE_HOURS,
    MIN_HOUR_COST_CENTS,
    Z_THRESHOLD,
    detect_agent_cost_anomalies_step,
)


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows if rows is not None else []
        self._scalar = scalar

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


class _RecordingSession:
    def __init__(self, calls: list[Any], results: list[_FakeResult] | None = None):
        self.calls = calls
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return self._results.pop(0) if self._results else self._default


class _ScopeCM:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    calls: list[Any] = []
    session = _RecordingSession(calls, results)
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))
    return session


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
async def test_no_findings_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _patch_scopes(monkeypatch, [_FakeResult(rows=[])])

    result = await detect_agent_cost_anomalies_step()

    assert result == {"status": "success", "anomalies": 0}
    assert len(session.calls) == 1  # only the findings scan — no rule churn
    _sql, binds = session.calls[0]
    assert binds["min_hours"] == MIN_BASELINE_HOURS
    assert binds["z"] == Z_THRESHOLD
    assert binds["min_cost"] == MIN_HOUR_COST_CENTS


@pytest.mark.asyncio
async def test_findings_land_in_alert_history(monkeypatch: pytest.MonkeyPatch) -> None:
    findings = [_finding(), _finding(agent_slug="storyboard")]
    session = _patch_scopes(
        monkeypatch,
        [
            _FakeResult(rows=findings),  # findings scan
            _FakeResult(scalar=42),  # anchor rule lookup — already exists
            _FakeResult(),  # alert_history insert #1
            _FakeResult(),  # alert_history insert #2
        ],
    )

    result = await detect_agent_cost_anomalies_step()

    assert result["anomalies"] == 2
    inserts = [c for c in session.calls if "INSERT INTO public.alert_history" in c[0]]
    assert len(inserts) == 2
    first = inserts[0][1]
    assert first["rule_id"] == 42  # anchored to the existing system rule
    assert first["metric_value"] == 14.0
    assert "script_ai" in first["message"]
    assert "800" in first["message"]
    assert first["notified"] is False


@pytest.mark.asyncio
async def test_anchor_rule_created_inactive_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _patch_scopes(
        monkeypatch,
        [
            _FakeResult(rows=[_finding()]),  # findings scan
            _FakeResult(scalar=None),  # anchor rule lookup — misses
            _FakeResult(scalar=99),  # anchor rule INSERT ... RETURNING id
            _FakeResult(),  # alert_history insert
        ],
    )

    await detect_agent_cost_anomalies_step()

    creates = [c for c in session.calls if "INSERT INTO public.alert_rules" in c[0]]
    assert len(creates) == 1
    # MUST be inactive — the admin /check loop walks active rules only,
    # and this rule has no evaluable metric there.
    assert creates[0][1]["is_active"] is False
    insert = [c for c in session.calls if "INSERT INTO public.alert_history" in c[0]][0]
    assert insert[1]["rule_id"] == 99
