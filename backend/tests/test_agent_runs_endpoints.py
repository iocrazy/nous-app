"""Unit tests for /ai-library/{runs,agents/:slug/runs,usage} endpoints.

Uses direct function calls with patched repositories rather than spinning
up FastAPI + test client. Focused on the endpoint-layer transformations:
row → response shape, 404 vs success, scope + month filter, pagination
param validation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.ai_library_router import (
    _month_bounds,
    _row_to_run_list_item,
    cancel_run,
    get_run,
    get_usage,
    list_agent_runs,
    list_run_children,
)


def _fake_auth(user_id: str | None = None):
    auth = MagicMock()
    auth.user_id = user_id or str(uuid4())
    return auth


def _sample_row(**overrides) -> dict:
    base = {
        "id": str(uuid4()),
        "agent_id": str(uuid4()),
        "user_id": str(uuid4()),
        "status": "completed",
        "trigger": "chat",
        "model": "qwen-max",
        "provider": "qwen",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cost_cents": 0.12,
        "started_at": datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc).isoformat(),
        "ended_at": datetime(2026, 4, 23, 10, 1, tzinfo=timezone.utc).isoformat(),
        "heartbeat_at": datetime(2026, 4, 23, 10, 1, tzinfo=timezone.utc).isoformat(),
        "cancel_requested": False,
        "created_at": datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc).isoformat(),
        "error_code": None,
        "skill_slugs_used": ["script-outline"],
        "metadata_json": {},
    }
    base.update(overrides)
    return base


# ------------------------------- helpers -------------------------------


def test_row_to_run_list_item_coerces_cost_to_float() -> None:
    row = _sample_row(cost_cents="0.12345")
    item = _row_to_run_list_item(row)
    assert item["cost_cents"] == 0.12345
    assert isinstance(item["cost_cents"], float)


def test_row_to_run_list_item_handles_null_cost() -> None:
    row = _sample_row(cost_cents=None)
    item = _row_to_run_list_item(row)
    assert item["cost_cents"] is None


def test_month_bounds_normal() -> None:
    start, end = _month_bounds("2026-04")
    assert start.startswith("2026-04-01")
    assert end.startswith("2026-05-01")


def test_month_bounds_december_rolls_year() -> None:
    start, end = _month_bounds("2026-12")
    assert start.startswith("2026-12-01")
    assert end.startswith("2027-01-01")


def test_month_bounds_invalid_format_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _month_bounds("2026/04")
    assert exc.value.status_code == 400


# ------------------------------- list runs -------------------------------


@pytest.mark.asyncio
async def test_list_agent_runs_404_when_agent_missing() -> None:
    with patch(
        "app.api.ai_library_router._repos",
        return_value=(
            MagicMock(get_by_slug=AsyncMock(return_value=None)),
            MagicMock(),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await list_agent_runs("nope", _fake_auth(), limit=10, offset=0)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_agent_runs_rejects_bad_pagination() -> None:
    with pytest.raises(HTTPException) as exc:
        await list_agent_runs("any", _fake_auth(), limit=0, offset=0)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await list_agent_runs("any", _fake_auth(), limit=500, offset=0)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_agent_runs_returns_paginated_envelope() -> None:
    agent_id = uuid4()
    mock_agent_repo = MagicMock(
        get_by_slug=AsyncMock(return_value={"id": str(agent_id), "slug": "x"})
    )
    page = {
        "items": [_sample_row(), _sample_row()],
        "total": 2,
    }
    with patch(
        "app.api.ai_library_router._repos",
        return_value=(mock_agent_repo, MagicMock()),
    ), patch(
        "app.api.ai_library_router.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.list_by_agent = AsyncMock(return_value=page)
        result = await list_agent_runs("x", _fake_auth(), limit=10, offset=0)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert result["limit"] == 10
    assert result["offset"] == 0


# ------------------------------- get run -------------------------------


@pytest.mark.asyncio
async def test_get_run_404_when_not_found() -> None:
    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        runs_cls.return_value.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await get_run(uuid4(), _fake_auth())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_run_normalizes_decimal_strings_to_float() -> None:
    row = _sample_row(
        cost_cents="0.5",
        prompt_cents_per_1k_snapshot="0.4",
        completion_cents_per_1k_snapshot="1.2",
    )
    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        runs_cls.return_value.get_by_id = AsyncMock(return_value=row)
        result = await get_run(uuid4(), _fake_auth())
    assert result["cost_cents"] == 0.5
    assert result["prompt_cents_per_1k_snapshot"] == 0.4


# ------------------------------- cancel run -------------------------------


@pytest.mark.asyncio
async def test_cancel_run_404_when_not_cancellable() -> None:
    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        runs_cls.return_value.request_cancel = AsyncMock(return_value=False)
        with pytest.raises(HTTPException) as exc:
            await cancel_run(uuid4(), _fake_auth())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_run_returns_accepted_on_success() -> None:
    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        runs_cls.return_value.request_cancel = AsyncMock(return_value=True)
        result = await cancel_run(uuid4(), _fake_auth())
    assert result["status"] == "cancel_requested"


# ------------------------------- usage -------------------------------


@pytest.mark.asyncio
async def test_get_usage_rejects_unknown_scope() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_usage(_fake_auth(), month="2026-04", scope="global")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_usage_team_requires_team_id() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_usage(_fake_auth(), month="2026-04", scope="team")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_usage_sums_per_agent_for_user_scope() -> None:
    user_id = str(uuid4())
    agent_a = str(uuid4())
    agent_b = str(uuid4())
    rows = [
        {
            "agent_id": agent_a,
            "user_id": user_id,
            "team_id": None,
            "project_id": None,
            "status": "completed",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cost_cents": 0.5,
        },
        {
            "agent_id": agent_a,
            "user_id": user_id,
            "team_id": None,
            "project_id": None,
            "status": "completed",
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_tokens": 300,
            "cost_cents": 1.0,
        },
        {
            "agent_id": agent_b,
            "user_id": user_id,
            "team_id": None,
            "project_id": None,
            "status": "failed",
            "prompt_tokens": 10,
            "completion_tokens": 0,
            "total_tokens": 10,
            "cost_cents": None,
        },
        # Wrong user — should be filtered out
        {
            "agent_id": agent_a,
            "user_id": str(uuid4()),
            "team_id": None,
            "project_id": None,
            "status": "completed",
            "prompt_tokens": 999,
            "completion_tokens": 999,
            "total_tokens": 1998,
            "cost_cents": 10.0,
        },
    ]

    auth = _fake_auth(user_id)
    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value={"slug": "x", "name": "X"})

    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls, patch(
        "app.api.ai_library_router._repos",
        return_value=(agent_repo, MagicMock()),
    ):
        runs_cls.return_value.monthly_usage_by_agent = AsyncMock(return_value=rows)
        result = await get_usage(auth, month="2026-04", scope="user")

    assert result["total_runs"] == 3  # excludes other-user row
    assert result["total_tokens"] == 460  # 150 + 300 + 10
    assert result["total_cost_cents"] == pytest.approx(1.5)
    per_agent = {b["agent_id"]: b for b in result["per_agent"]}
    assert per_agent[agent_a]["run_count"] == 2
    assert per_agent[agent_b]["failed_count"] == 1


# ─── Phase 4 of #199: list_run_children ────────────────────────────────


def test_row_to_run_list_item_includes_parent_run_id() -> None:
    """Sub-runs spawned via the Task tool carry parent_run_id; the
    list projection must surface it so the Runs UI can render the
    tree without a second round-trip."""
    parent = str(uuid4())
    row = _sample_row(parent_run_id=parent)
    item = _row_to_run_list_item(row)
    assert item["parent_run_id"] == parent


def test_row_to_run_list_item_parent_null_for_top_level() -> None:
    """Top-level runs have NULL parent_run_id. The projection must
    pass NULL through cleanly — UI treats NULL as 'this is a root'."""
    row = _sample_row()  # no parent_run_id key set
    item = _row_to_run_list_item(row)
    assert item["parent_run_id"] is None


@pytest.mark.asyncio
async def test_list_run_children_404_when_parent_missing() -> None:
    """Stray parent_run_id from another user must read as 404 — the
    same authz pattern as get_run, so existence isn't leaked via
    'children empty list' vs 'parent doesn't exist'."""
    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        runs_cls.return_value.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await list_run_children(uuid4(), _fake_auth())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_run_children_returns_empty_when_no_subruns() -> None:
    """A parent that exists but never spawned a sub-agent has no
    children — return empty list, not 404."""
    parent_id = uuid4()
    parent_row = _sample_row(id=str(parent_id))
    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        instance = runs_cls.return_value
        instance.get_by_id = AsyncMock(return_value=parent_row)
        instance.list_children = AsyncMock(return_value=[])
        out = await list_run_children(parent_id, _fake_auth())
    assert out == []


@pytest.mark.asyncio
async def test_list_run_children_returns_slim_envelope() -> None:
    """Children come back through the slim _row_to_run_list_item
    projection — same shape as the agents/{slug}/runs list endpoint
    so the Runs UI can use one render path for both."""
    parent_id = uuid4()
    parent_row = _sample_row(id=str(parent_id))
    child_row = _sample_row(
        id=str(uuid4()),
        parent_run_id=str(parent_id),
        trigger="subagent_task",
    )

    with patch("app.api.ai_library_router.AgentRunsRepository") as runs_cls:
        instance = runs_cls.return_value
        instance.get_by_id = AsyncMock(return_value=parent_row)
        instance.list_children = AsyncMock(return_value=[child_row])
        out = await list_run_children(parent_id, _fake_auth())

    assert len(out) == 1
    assert out[0]["parent_run_id"] == str(parent_id)
    assert out[0]["trigger"] == "subagent_task"
    # Slim projection — heavy fields like metadata_json must NOT be
    # in the list response (token cost in the UI render).
    assert "metadata_json" not in out[0]
