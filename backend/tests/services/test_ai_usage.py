"""Unit tests for the W3c AI usage recording + budget breaker service.

Phase B5 Task 1: ``record_usage`` / ``get_team_month_spend_cents`` /
``get_team_budget`` / ``upsert_team_budget`` were migrated from raw
``app.db.engine`` calls to the SQLAlchemy ORM (``app.db.session.read_scope`` /
``write_scope``). Tests patch those scopes and assert against the COMPILED
statement (postgresql dialect) rather than reconstructing SQL strings —
mirrors tests/test_orm_b4_task1_compile_coverage.py.
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.services import ai_usage


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

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _RecordingSession:
    def __init__(self, result: _FakeResult | None = None) -> None:
        self.calls: list[Any] = []
        self._result = result or _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._result


def _patch_scope(monkeypatch: pytest.MonkeyPatch, attr: str, result=None):
    session = _RecordingSession(result)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(f"app.services.ai_usage.{attr}", fake_scope)
    return session


def test_attribution_from_origin_kind():
    assert ai_usage.attribution_from_origin_kind("routine") == "rule_owner"
    assert ai_usage.attribution_from_origin_kind("pipeline") == "rule_owner"
    assert ai_usage.attribution_from_origin_kind("manual") == "direct_human"
    assert ai_usage.attribution_from_origin_kind("chat_delegate") == "direct_human"
    assert ai_usage.attribution_from_origin_kind(None) == "direct_human"


@pytest.mark.asyncio
async def test_record_usage_upserts_rollup_with_truncated_bucket(monkeypatch):
    session = _patch_scope(monkeypatch, "write_scope")

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

    assert len(session.calls) == 1
    sql, binds = _compile(session.calls[0])
    assert "INSERT INTO public.ai_usage_hourly" in sql
    assert "ON CONFLICT ON CONSTRAINT ai_usage_hourly_dims_uq DO UPDATE SET" in sql
    # additive accumulators add EXCLUDED onto the existing row
    assert (
        "prompt_tokens = (public.ai_usage_hourly.prompt_tokens + "
        "excluded.prompt_tokens)" in sql
    )
    assert (
        "completion_tokens = (public.ai_usage_hourly.completion_tokens + "
        "excluded.completion_tokens)" in sql
    )
    assert (
        "cached_input_tokens = (public.ai_usage_hourly.cached_input_tokens + "
        "excluded.cached_input_tokens)" in sql
    )
    assert (
        "cost_cents = (public.ai_usage_hourly.cost_cents + excluded.cost_cents)" in sql
    )
    assert (
        "event_count = (public.ai_usage_hourly.event_count + excluded.event_count)"
        in sql
    )

    # bucket truncated to the hour
    assert binds["bucket_hour"] == occurred.replace(minute=0, second=0, microsecond=0)
    assert binds["team_id"] == 900123456789 and isinstance(binds["team_id"], int)
    assert binds["project_id"] is None
    assert binds["agent_id"] == UUID("11111111-1111-1111-1111-111111111111")
    assert binds["module"] == "issue_dispatch"
    assert binds["attribution"] == "rule_owner"
    assert binds["prompt_tokens"] == 100 and binds["completion_tokens"] == 40
    assert binds["cached_input_tokens"] == 5
    assert binds["cost_cents"] == Decimal("1.2345")
    assert binds["event_count"] == 1


@pytest.mark.asyncio
async def test_record_usage_defaults_and_null_cost(monkeypatch):
    session = _patch_scope(monkeypatch, "write_scope")

    await ai_usage.record_usage(
        module="chat",
        attribution=None,  # → direct_human
        prompt_tokens=10,
        completion_tokens=0,
        cost_cents=None,  # unpriced → 0 in the rollup
    )
    _sql, binds = _compile(session.calls[0])
    assert binds["attribution"] == "direct_human"
    assert binds["cost_cents"] == Decimal(0)


@pytest.mark.asyncio
async def test_record_usage_never_raises(monkeypatch):
    @asynccontextmanager
    async def boom_scope():
        raise RuntimeError("db down")
        yield  # pragma: no cover — unreachable, keeps this a generator

    monkeypatch.setattr("app.services.ai_usage.write_scope", boom_scope)
    # Must swallow — a telemetry failure cannot break the AI call.
    await ai_usage.record_usage(
        module="chat", attribution="direct_human", prompt_tokens=1, completion_tokens=1
    )


@pytest.mark.asyncio
async def test_get_team_month_spend_cents_sums_current_month(monkeypatch):
    session = _patch_scope(
        monkeypatch, "read_scope", _FakeResult(scalar=Decimal("123.45"))
    )
    result = await ai_usage.get_team_month_spend_cents("900")
    assert result == Decimal("123.45")

    sql, binds = _compile(session.calls[0])
    assert "coalesce(sum(public.ai_usage_hourly.cost_cents)" in sql
    assert "public.ai_usage_hourly.bucket_hour >= date_trunc" in sql
    assert binds["team_id_1"] == 900


@pytest.mark.asyncio
async def test_get_team_month_spend_cents_returns_zero_when_no_spend(monkeypatch):
    _patch_scope(monkeypatch, "read_scope", _FakeResult(scalar=None))
    result = await ai_usage.get_team_month_spend_cents("900")
    assert result == Decimal(0)


@pytest.mark.asyncio
async def test_get_team_budget_returns_row_as_dict(monkeypatch):
    now = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)
    row = {
        "team_id": 900,
        "monthly_budget_cents": Decimal("500"),
        "updated_by_user_id": None,
        "created_at": now,
        "updated_at": now,
    }
    session = _patch_scope(monkeypatch, "read_scope", _FakeResult(rows=[row]))
    result = await ai_usage.get_team_budget("900")
    assert result == row

    sql, binds = _compile(session.calls[0])
    assert "SELECT public.team_ai_budgets.team_id" in sql
    assert binds["team_id_1"] == 900


@pytest.mark.asyncio
async def test_get_team_budget_returns_none_when_unset(monkeypatch):
    _patch_scope(monkeypatch, "read_scope", _FakeResult(rows=[]))
    result = await ai_usage.get_team_budget("900")
    assert result is None


@pytest.mark.asyncio
async def test_upsert_team_budget_upserts_and_returns_row(monkeypatch):
    now = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)
    uid = "22222222-2222-2222-2222-222222222222"
    returned_row = {
        "team_id": 900,
        "monthly_budget_cents": Decimal("1000"),
        "updated_by_user_id": UUID(uid),
        "created_at": now,
        "updated_at": now,
    }
    session = _patch_scope(monkeypatch, "write_scope", _FakeResult(rows=[returned_row]))
    result = await ai_usage.upsert_team_budget(
        "900", monthly_budget_cents=1000, updated_by_user_id=uid
    )
    assert result == returned_row

    sql, binds = _compile(session.calls[0])
    assert "INSERT INTO public.team_ai_budgets" in sql
    assert "ON CONFLICT (team_id) DO UPDATE SET" in sql
    assert "RETURNING public.team_ai_budgets.team_id" in sql
    assert binds["team_id"] == 900
    assert binds["monthly_budget_cents"] == Decimal("1000")
    assert binds["updated_by_user_id"] == UUID(uid)


@pytest.mark.asyncio
async def test_upsert_team_budget_null_budget_means_unlimited(monkeypatch):
    session = _patch_scope(monkeypatch, "write_scope", _FakeResult(rows=[]))
    result = await ai_usage.upsert_team_budget(
        "900", monthly_budget_cents=None, updated_by_user_id=None
    )
    assert result is None

    _sql, binds = _compile(session.calls[0])
    assert binds["monthly_budget_cents"] is None
    assert binds["updated_by_user_id"] is None


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
