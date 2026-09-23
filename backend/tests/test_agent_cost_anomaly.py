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

import importlib
from typing import Any

import pytest
from loguru import logger
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows.agent_cost_anomaly import (
    DEFAULT_MIN_HOUR_COST_CENTS,
    MIN_BASELINE_HOURS,
    MIN_HOUR_COST_SETTING_KEY,
    Z_THRESHOLD,
    detect_agent_cost_anomalies_step,
)

# The step's first read is the admin floor from system_settings; ``None``
# = key missing (the normal unconfigured state).
_NO_FLOOR_SETTING = None


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
    session = _patch_scopes(
        monkeypatch,
        [_FakeResult(scalar=_NO_FLOOR_SETTING), _FakeResult(rows=[])],
    )

    result = await detect_agent_cost_anomalies_step()

    assert result == {"status": "success", "anomalies": 0}
    # floor-setting read + the findings scan — no rule churn
    assert len(session.calls) == 2
    _sql, binds = session.calls[1]
    assert binds["min_hours"] == MIN_BASELINE_HOURS
    assert binds["z"] == Z_THRESHOLD
    assert binds["min_cost"] == DEFAULT_MIN_HOUR_COST_CENTS


@pytest.mark.asyncio
async def test_findings_land_in_alert_history(monkeypatch: pytest.MonkeyPatch) -> None:
    findings = [_finding(), _finding(agent_slug="storyboard")]
    session = _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=_NO_FLOOR_SETTING),  # floor setting read
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
            _FakeResult(scalar=_NO_FLOOR_SETTING),  # floor setting read
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


# ── Admin-configurable floor ─────────────────────────────────────────────────


def _findings_min_cost(session: _RecordingSession) -> float:
    scans = [c for c in session.calls if "min_cost" in c[1]]
    assert len(scans) == 1
    return scans[0][1]["min_cost"]


@pytest.fixture
def warnings_sink():
    messages: list[str] = []
    handler_id = logger.add(
        lambda m: messages.append(m.record["message"]), level="WARNING"
    )
    yield messages
    logger.remove(handler_id)


def test_default_floor_is_half_a_cent() -> None:
    assert DEFAULT_MIN_HOUR_COST_CENTS == 0.5
    assert MIN_HOUR_COST_SETTING_KEY == "agent_cost_anomaly.min_hour_cost_cents"


@pytest.mark.asyncio
async def test_floor_setting_is_read_from_system_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _patch_scopes(
        monkeypatch, [_FakeResult(scalar="2"), _FakeResult(rows=[])]
    )

    await detect_agent_cost_anomalies_step()

    setting_sql, setting_binds = session.calls[0]
    assert "system_settings" in setting_sql
    assert MIN_HOUR_COST_SETTING_KEY in setting_binds.values()
    assert _findings_min_cost(session) == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['"2"', 2, 2.0])
async def test_floor_setting_accepts_jsonb_shapes(
    monkeypatch: pytest.MonkeyPatch, raw: object
) -> None:
    session = _patch_scopes(monkeypatch, [_FakeResult(scalar=raw), _FakeResult()])

    await detect_agent_cost_anomalies_step()

    assert _findings_min_cost(session) == 2.0


@pytest.mark.asyncio
async def test_missing_floor_setting_uses_default_quietly(
    monkeypatch: pytest.MonkeyPatch, warnings_sink: list[str]
) -> None:
    session = _patch_scopes(
        monkeypatch, [_FakeResult(scalar=None), _FakeResult(rows=[])]
    )

    await detect_agent_cost_anomalies_step()

    assert _findings_min_cost(session) == 0.5
    assert not any(MIN_HOUR_COST_SETTING_KEY in m for m in warnings_sink)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["abc", "-1", "nan", True, ""])
async def test_invalid_floor_setting_uses_default_and_warns(
    monkeypatch: pytest.MonkeyPatch, warnings_sink: list[str], raw: object
) -> None:
    session = _patch_scopes(monkeypatch, [_FakeResult(scalar=raw), _FakeResult()])

    await detect_agent_cost_anomalies_step()

    assert _findings_min_cost(session) == 0.5
    assert any(MIN_HOUR_COST_SETTING_KEY in m for m in warnings_sink)


# ── Admin router: GET/PUT /admin/settings/agent-cost-anomaly ─────────────────


class _StubSettingsRepo:
    """Stands in for SystemSettingsRepository; its store also backs reads."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def upsert_setting(self, key: str, value: Any, updated_by: str) -> dict:
        self.store = {**self.store, key: value}
        return {"key": key, "value": value}


def _patch_router_repo(monkeypatch: pytest.MonkeyPatch) -> _StubSettingsRepo:
    # ``app.api.admin`` re-exports the APIRouter under this same name, so
    # attribute-style import would hand back the router, not the module.
    settings_router = importlib.import_module("app.api.admin.settings_router")

    repo = _StubSettingsRepo()

    class _StoreScope:
        async def __aenter__(self) -> "_StoreScope":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def execute(self, stmt: Any) -> _FakeResult:
            _sql, binds = _compile(stmt)
            key = next(v for v in binds.values() if isinstance(v, str))
            return _FakeResult(scalar=repo.store.get(key))

    monkeypatch.setattr(db_session, "read_scope", lambda: _StoreScope())
    monkeypatch.setattr(settings_router, "get_system_settings_repository", lambda: repo)

    async def _no_audit(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(settings_router, "create_audit_log", _no_audit)
    return repo


class _Auth:
    user_id = "admin-1"


@pytest.mark.asyncio
async def test_router_get_returns_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.admin.settings_router import get_agent_cost_anomaly_config

    _patch_router_repo(monkeypatch)

    result = await get_agent_cost_anomaly_config(_Auth())

    assert result.min_hour_cost_cents == 0.5


@pytest.mark.asyncio
async def test_router_put_persists_and_get_reflects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.admin.settings_router import (
        get_agent_cost_anomaly_config,
        update_agent_cost_anomaly_config,
    )
    from app.schemas.admin import AgentCostAnomalyConfig

    repo = _patch_router_repo(monkeypatch)

    put = await update_agent_cost_anomaly_config(
        AgentCostAnomalyConfig(min_hour_cost_cents=3.25), _Auth()
    )
    got = await get_agent_cost_anomaly_config(_Auth())

    assert put.min_hour_cost_cents == 3.25
    assert repo.store == {MIN_HOUR_COST_SETTING_KEY: 3.25}
    assert got.min_hour_cost_cents == 3.25


@pytest.mark.parametrize("bad", [-0.01, float("nan"), float("inf")])
def test_router_body_rejects_negative_and_non_finite(bad: float) -> None:
    from pydantic import ValidationError

    from app.schemas.admin import AgentCostAnomalyConfig

    with pytest.raises(ValidationError):
        AgentCostAnomalyConfig(min_hour_cost_cents=bad)
