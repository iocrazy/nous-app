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
    undo_run,
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
    with (
        patch(
            "app.api.ai_library_router._repos",
            return_value=(mock_agent_repo, MagicMock()),
        ),
        patch("app.repositories.agent_runs_repository.AgentRunsRepository") as runs_cls,
    ):
        runs_cls.return_value.list_by_agent = AsyncMock(return_value=page)
        result = await list_agent_runs("x", _fake_auth(), limit=10, offset=0)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert result["limit"] == 10
    assert result["offset"] == 0


@pytest.mark.asyncio
async def test_list_agent_runs_passes_conversation_filter() -> None:
    """The grouped view's expand path: conversation_id must reach the repo."""
    agent_id = uuid4()
    mock_agent_repo = MagicMock(
        get_by_slug=AsyncMock(return_value={"id": str(agent_id), "slug": "x"})
    )
    with (
        patch(
            "app.api.ai_library_router._repos",
            return_value=(mock_agent_repo, MagicMock()),
        ),
        patch("app.repositories.agent_runs_repository.AgentRunsRepository") as runs_cls,
    ):
        runs_cls.return_value.list_by_agent = AsyncMock(
            return_value={"items": [], "total": 0}
        )
        await list_agent_runs(
            "x", _fake_auth(), limit=10, offset=0, conversation_id="123456789"
        )
        kwargs = runs_cls.return_value.list_by_agent.call_args.kwargs
    assert kwargs["conversation_id"] == "123456789"


@pytest.mark.asyncio
async def test_list_agent_runs_rejects_non_numeric_conversation_id() -> None:
    """Snowflake ids are numeric strings; anything else is a 400, not a
    swallowed SQL error."""
    with pytest.raises(HTTPException) as exc:
        await list_agent_runs(
            "any", _fake_auth(), limit=10, offset=0, conversation_id="abc"
        )
    assert exc.value.status_code == 400


# ------------------------------- run groups -------------------------------


def _sample_group_row(**overrides) -> dict:
    base = {
        "group_key": "conv:900001",
        "conversation_id": "900001",
        "run_count": 3,
        "prompt_tokens": 300,
        "completion_tokens": 150,
        "cost_cents": "0.5",  # numeric arrives as Decimal/str from PG
        "first_started_at": datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        "last_started_at": datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        "any_running": False,
        "error_count": 0,
        "latest_run_id": "800001",
        "latest_status": "completed",
        "trigger": "chat",
        "model": "qwen-max",
        "latest_output_summary": "Sure — here is the analysis.",
        "latest_error_code": None,
        "latest_ended_at": datetime(2026, 7, 3, 10, 1, tzinfo=timezone.utc),
        "title": "Second act, draft three",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_run_groups_404_when_agent_missing() -> None:
    from app.api.ai_library_router import list_agent_run_groups

    with patch(
        "app.api.ai_library_router._repos",
        return_value=(
            MagicMock(get_by_slug=AsyncMock(return_value=None)),
            MagicMock(),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await list_agent_run_groups("nope", _fake_auth(), limit=10, offset=0)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_run_groups_rejects_bad_pagination() -> None:
    from app.api.ai_library_router import list_agent_run_groups

    with pytest.raises(HTTPException) as exc:
        await list_agent_run_groups("any", _fake_auth(), limit=0, offset=0)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await list_agent_run_groups("any", _fake_auth(), limit=10, offset=-1)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_run_groups_envelope_and_cost_coercion() -> None:
    """Groups page returns the envelope, coerces numeric cost to float, and
    total counts GROUPS (repo's number passes through untouched)."""
    from app.api.ai_library_router import list_agent_run_groups

    agent_id = uuid4()
    mock_agent_repo = MagicMock(
        get_by_slug=AsyncMock(return_value={"id": str(agent_id), "slug": "x"})
    )
    page = {
        "items": [
            _sample_group_row(),
            _sample_group_row(
                group_key="run:800002",
                conversation_id=None,
                run_count=1,
                cost_cents=None,
                trigger="visual_analysis",
            ),
        ],
        "total": 2,
    }
    with (
        patch(
            "app.api.ai_library_router._repos",
            return_value=(mock_agent_repo, MagicMock()),
        ),
        patch("app.repositories.agent_runs_repository.AgentRunsRepository") as runs_cls,
    ):
        runs_cls.return_value.list_groups_by_agent = AsyncMock(return_value=page)
        result = await list_agent_run_groups("x", _fake_auth(), limit=10, offset=0)

    assert result["total"] == 2
    grouped, single = result["items"]
    assert grouped["cost_cents"] == 0.5
    assert isinstance(grouped["cost_cents"], float)
    assert grouped["run_count"] == 3
    assert single["conversation_id"] is None
    assert single["cost_cents"] is None


@pytest.mark.asyncio
async def test_list_run_groups_carries_title_through_the_envelope() -> None:
    """The row's display name is projected by the repo and must survive the
    router + response_model hop, including the NULL case.

    Without it the client falls back to ``latest_output_summary``, which on
    prod data is empty or a JSON payload for two thirds of runs — the
    "every conversation is called Untitled" report. NULL is a legitimate
    value (a captioning run names no conversation); the client owns the
    translated fallback label, so the field must not be coerced to "" here."""
    from app.api.ai_library_router import list_agent_run_groups
    from app.schemas.agent_runs import RunGroupListResponse

    agent_id = uuid4()
    mock_agent_repo = MagicMock(
        get_by_slug=AsyncMock(return_value={"id": str(agent_id), "slug": "x"})
    )
    page = {
        "items": [
            _sample_group_row(title="Write the opening scene of the pilot"),
            # Pipeline run: no conversation, no issue, no user message.
            _sample_group_row(
                group_key="run:800002",
                conversation_id=None,
                trigger="visual_analysis_l1",
                title=None,
            ),
        ],
        "total": 2,
    }
    with (
        patch(
            "app.api.ai_library_router._repos",
            return_value=(mock_agent_repo, MagicMock()),
        ),
        patch("app.repositories.agent_runs_repository.AgentRunsRepository") as runs_cls,
    ):
        runs_cls.return_value.list_groups_by_agent = AsyncMock(return_value=page)
        result = await list_agent_run_groups("x", _fake_auth(), limit=10, offset=0)

    named, unnamed = result["items"]
    assert named["title"] == "Write the opening scene of the pilot"
    assert unnamed["title"] is None

    # The declared response_model is what actually reaches the browser —
    # a field the router forwards but the schema drops is invisible to it.
    validated = RunGroupListResponse.model_validate(result)
    assert validated.items[0].title == "Write the opening scene of the pilot"
    assert validated.items[1].title is None


# ------------------------------- get run -------------------------------


@pytest.mark.asyncio
async def test_get_run_404_when_not_found() -> None:
    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
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
    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.get_by_id = AsyncMock(return_value=row)
        result = await get_run(uuid4(), _fake_auth())
    assert result["cost_cents"] == 0.5
    assert result["prompt_cents_per_1k_snapshot"] == 0.4


# ------------------------------- cancel run -------------------------------


@pytest.mark.asyncio
async def test_cancel_run_404_when_not_cancellable() -> None:
    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.request_cancel = AsyncMock(return_value=False)
        with pytest.raises(HTTPException) as exc:
            await cancel_run(uuid4(), _fake_auth())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_run_returns_accepted_on_success() -> None:
    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.request_cancel = AsyncMock(return_value=True)
        result = await cancel_run(uuid4(), _fake_auth())
    assert result["status"] == "cancel_requested"


# ---------------------------- undo run ----------------------------


@pytest.mark.asyncio
async def test_undo_run_404_when_not_found() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="not_found"))
    with patch(
        "app.api.ai_library_router.get_agent_runs_repository", return_value=repo
    ):
        with pytest.raises(HTTPException) as exc:
            await undo_run("123", _fake_auth())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_undo_run_409_while_running() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="running"))
    with patch(
        "app.api.ai_library_router.get_agent_runs_repository", return_value=repo
    ):
        with pytest.raises(HTTPException) as exc:
            await undo_run("123", _fake_auth())
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_undo_run_second_call_reports_already_undone_without_executing() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="already_undone"))
    execute = AsyncMock()
    with (
        patch("app.api.ai_library_router.get_agent_runs_repository", return_value=repo),
        patch("app.services.ai.undo.run_undo_service.execute_undo", execute),
    ):
        out = await undo_run("123", _fake_auth())
    assert out["status"] == "already_undone"
    assert out["skipped"] == []
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_undo_run_claimed_executes_and_returns_typed_report() -> None:
    repo = MagicMock(claim_undo=AsyncMock(return_value="claimed"))
    report = {
        "shots_deleted": 2,
        "shots_reverted": 1,
        "scene_elements_reverted": 3,
        "skipped": [{"kind": "shot", "id": "900", "reason": "rendered"}],
    }
    with (
        patch("app.api.ai_library_router.get_agent_runs_repository", return_value=repo),
        patch(
            "app.services.ai.undo.run_undo_service.execute_undo",
            AsyncMock(return_value=report),
        ),
    ):
        out = await undo_run("800100000000000009", _fake_auth())
    assert out["status"] == "done"
    assert out["shots_deleted"] == 2
    assert out["skipped"][0]["reason"] == "rendered"


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

    with (
        patch("app.repositories.agent_runs_repository.AgentRunsRepository") as runs_cls,
        patch(
            "app.api.ai_library_router._repos",
            return_value=(agent_repo, MagicMock()),
        ),
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
    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
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
    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
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

    with patch(
        "app.repositories.agent_runs_repository.AgentRunsRepository"
    ) as runs_cls:
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
