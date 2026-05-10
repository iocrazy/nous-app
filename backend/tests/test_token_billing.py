"""Phase 3 — token billing reconciliation + summary tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.billing import token_billing as tb

# ─── summarize_user_usage ────────────────────────────────────────────


def _row(model="qwen-max", tokens=100, cost=1.5, days_ago=0):
    return {
        "model": model,
        "total_tokens": tokens,
        "cost_points": cost,
        "created_at": (
            datetime.now(timezone.utc) - timedelta(days=days_ago)
        ).isoformat(),
    }


def _fake_client_returning(rows):
    client = MagicMock()
    table_q = MagicMock()
    table_q.select.return_value = table_q
    table_q.eq.return_value = table_q
    table_q.gte.return_value = table_q
    table_q.lte.return_value = table_q
    table_q.limit.return_value = table_q
    table_q.execute = AsyncMock(return_value=MagicMock(data=rows))
    client.table = MagicMock(return_value=table_q)
    return client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_aggregates_by_model_and_day():
    rows = [
        _row("qwen-max", tokens=100, cost=1.0, days_ago=0),
        _row("qwen-max", tokens=200, cost=2.0, days_ago=0),
        _row("claude-sonnet", tokens=300, cost=5.0, days_ago=1),
    ]
    client = _fake_client_returning(rows)
    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        s = await tb.summarize_user_usage(uuid4(), days=7)

    assert s.overall_total_tokens == 600
    assert s.overall_cost_points == 8.0
    assert s.overall_run_count == 3
    # by_model sorted by cost desc — claude (5) before qwen (3)
    assert s.by_model[0].model == "claude-sonnet"
    assert s.by_model[0].cost_points == 5.0
    assert s.by_model[1].model == "qwen-max"
    assert s.by_model[1].cost_points == 3.0
    assert s.by_model[1].run_count == 2
    # by_day sorted ascending (chronological)
    assert len(s.by_day) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_handles_empty_rows():
    client = _fake_client_returning([])
    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        s = await tb.summarize_user_usage(uuid4(), days=7)
    assert s.overall_total_tokens == 0
    assert s.overall_cost_points == 0.0
    assert s.by_model == []
    assert s.by_day == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_swallows_db_error():
    """DB failure → empty summary, not crash."""
    client = MagicMock()
    table_q = MagicMock()
    table_q.select.return_value = table_q
    table_q.eq.return_value = table_q
    table_q.gte.return_value = table_q
    table_q.lte.return_value = table_q
    table_q.limit.return_value = table_q
    table_q.execute = AsyncMock(side_effect=RuntimeError("supabase down"))
    client.table = MagicMock(return_value=table_q)

    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        s = await tb.summarize_user_usage(uuid4(), days=7)
    assert s.overall_run_count == 0


# ─── reconcile_run ───────────────────────────────────────────────────


def _ok_insert_client():
    """Build a client whose table().insert().execute() succeeds."""
    client = MagicMock()
    table_q = MagicMock()
    insert_q = MagicMock()
    insert_q.execute = AsyncMock(return_value=MagicMock(data=[]))
    table_q.insert = MagicMock(return_value=insert_q)
    client.table = MagicMock(return_value=table_q)
    return client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_byo_key_skips_points():
    """BYO-key runs log usage but never charge points."""
    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=_ok_insert_client()),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="custom-byo-model",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=5.0,
            byo_key=True,
        )
    assert result.byo_key is True
    assert result.charged is False
    assert result.charged_points == 0.0
    assert result.usage_logged is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_no_team_skips_points():
    """Personal-scope run (no team_id) just logs."""
    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=_ok_insert_client()),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=None,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=5.0,
            byo_key=False,
        )
    assert result.charged is False
    assert "no team_id" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_zero_cost_skips_points():
    """Free run (cost_points=0) doesn't try to charge."""
    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=_ok_insert_client()),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=0.0,
            byo_key=False,
        )
    assert result.charged is False
    assert "zero cost" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_platform_run_calls_points_service():
    """Non-BYO + team + cost > 0 → calls PointsService.check_and_consume."""
    fake_ps = MagicMock()
    fake_ps.check_and_consume = AsyncMock(return_value=True)

    with (
        patch(
            "app.services.ai.billing.token_billing.get_async_supabase_admin",
            AsyncMock(return_value=_ok_insert_client()),
        ),
        patch(
            "app.services.billing.points_service.PointsService", return_value=fake_ps
        ),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="nous_qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=2.5,
            byo_key=False,
        )
    assert result.charged is True
    assert result.charged_points == 2.5
    fake_ps.check_and_consume.assert_awaited_once()
    # Must include team_id + run metadata
    _, kwargs = fake_ps.check_and_consume.call_args
    assert kwargs["team_id"] == "42"
    assert "run_id" in kwargs["metadata"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_points_failure_does_not_raise():
    """PointsService raising → ReconcileResult with charged=False, not exception."""
    fake_ps = MagicMock()
    fake_ps.check_and_consume = AsyncMock(
        side_effect=RuntimeError("insufficient balance")
    )

    with (
        patch(
            "app.services.ai.billing.token_billing.get_async_supabase_admin",
            AsyncMock(return_value=_ok_insert_client()),
        ),
        patch(
            "app.services.billing.points_service.PointsService", return_value=fake_ps
        ),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=42,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=2.0,
            byo_key=False,
        )
    assert result.charged is False
    assert result.usage_logged is True  # log still wrote
    assert "errored" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_log_failure_still_returns():
    """ai_usage_logs insert failing → usage_logged=False, but caller still
    gets a ReconcileResult (no exception)."""
    bad_client = MagicMock()
    bad_table = MagicMock()
    bad_insert = MagicMock()
    bad_insert.execute = AsyncMock(side_effect=RuntimeError("no table"))
    bad_table.insert = MagicMock(return_value=bad_insert)
    bad_client.table = MagicMock(return_value=bad_table)

    with patch(
        "app.services.ai.billing.token_billing.get_async_supabase_admin",
        AsyncMock(return_value=bad_client),
    ):
        result = await tb.reconcile_run(
            run_id=uuid4(),
            user_id=uuid4(),
            team_id=None,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=1,
            completion_tokens=1,
            cost_points=0.0,
            byo_key=True,
        )
    assert result.usage_logged is False
    assert result.byo_key is True
