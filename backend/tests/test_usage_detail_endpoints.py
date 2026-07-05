"""User-facing usage detail endpoints (/usage/runs, /usage/daily): hard
user-scoping, agent enrichment, duration derivation, daily rollup totals."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

USER_ID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "22222222-2222-2222-2222-222222222222"


def _auth():
    auth = MagicMock()
    auth.user_id = USER_ID
    return auth


@pytest.mark.asyncio
async def test_usage_runs_forces_caller_user_scope():
    """The repo call must receive the CALLER's uuid — never a client-supplied
    user filter (there is no such query param)."""
    from app.api.ai_library_router import get_usage_runs

    captured = {}

    async def _fake_list(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0}

    with patch("app.api.ai_library_router.get_agent_runs_repository") as repo_factory:
        repo = MagicMock()
        repo.list_runs_admin = AsyncMock(side_effect=_fake_list)
        repo_factory.return_value = repo

        resp = await get_usage_runs(_auth(), page=2, page_size=10, days=7)

    assert captured["user_id"] == UUID(USER_ID)
    assert captured["offset"] == 10
    assert captured["limit"] == 10
    assert captured["started_after"] is not None
    assert resp["total"] == 0 and resp["page"] == 2


@pytest.mark.asyncio
async def test_usage_runs_enriches_agent_and_duration():
    from app.api.ai_library_router import get_usage_runs

    repo_rows = {
        "items": [
            {
                "id": 9,
                "user_id": USER_ID,
                "agent_id": AGENT_ID,
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "status": "completed",
                "trigger": "chat",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cost_cents": "0.9",
                "started_at": "2026-07-04T00:00:00+00:00",
                "ended_at": "2026-07-04T00:00:03+00:00",
                "error_code": None,
            }
        ],
        "total": 1,
    }

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(
        return_value={"slug": "script-ai", "name": "Script AI"}
    )

    with (
        patch("app.api.ai_library_router.get_agent_runs_repository") as repo_factory,
        patch("app.api.ai_library_router._repos", return_value=(agent_repo, None)),
    ):
        repo = MagicMock()
        repo.list_runs_admin = AsyncMock(return_value=repo_rows)
        repo_factory.return_value = repo

        resp = await get_usage_runs(_auth())

    item = resp["items"][0]
    assert item["agent_slug"] == "script-ai"
    assert item["agent_name"] == "Script AI"
    assert item["model"] == "deepseek-v4-flash"
    assert item["cost_cents"] == 0.9
    assert item["duration_ms"] == 3000


@pytest.mark.asyncio
async def test_usage_daily_scopes_and_totals():
    from app.api.ai_library_router import get_usage_daily

    captured = {}

    async def _fake_daily(**kwargs):
        captured.update(kwargs)
        return [
            {
                "date": "2026-07-03",
                "key": "deepseek-v4-flash",
                "requests": 10,
                "failed_requests": 3,
                "prompt_tokens": 30000,
                "completion_tokens": 7000,
                "total_tokens": 37000,
                "cost_cents": "3.1",
            },
            {
                "date": "2026-07-04",
                "key": "doubao-seed-2-0-lite-260428",
                "requests": 2,
                "failed_requests": 0,
                "prompt_tokens": 2000,
                "completion_tokens": 588,
                "total_tokens": 2588,
                "cost_cents": "0",
            },
        ]

    with patch("app.api.ai_library_router.get_agent_runs_repository") as repo_factory:
        repo = MagicMock()
        repo.daily_usage = AsyncMock(side_effect=_fake_daily)
        repo_factory.return_value = repo

        resp = await get_usage_daily(_auth(), days=30)

    assert captured["user_id"] == UUID(USER_ID)
    assert captured["group_by"] == "model"
    assert resp["group_by"] == "model"
    assert resp["total_requests"] == 12
    assert resp["total_failed"] == 3
    assert resp["total_tokens"] == 39588
    assert resp["total_cost_cents"] == pytest.approx(3.1)
    # model keys label as themselves
    assert resp["daily"][0]["label"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_usage_daily_agent_grouping_enriches_labels():
    """group_by=agent buckets carry uuids — endpoint resolves display names."""
    from app.api.ai_library_router import get_usage_daily

    async def _fake_daily(**kwargs):
        return [
            {
                "date": "2026-07-04",
                "key": AGENT_ID,
                "requests": 4,
                "failed_requests": 1,
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost_cents": "0.2",
            }
        ]

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(
        return_value={"slug": "topic-scorer", "name": "Topic-Scorer"}
    )

    with (
        patch("app.api.ai_library_router.get_agent_runs_repository") as repo_factory,
        patch("app.api.ai_library_router._repos", return_value=(agent_repo, None)),
    ):
        repo = MagicMock()
        repo.daily_usage = AsyncMock(side_effect=_fake_daily)
        repo_factory.return_value = repo

        resp = await get_usage_daily(_auth(), days=7, group_by="agent")

    assert resp["group_by"] == "agent"
    assert resp["daily"][0]["label"] == "Topic-Scorer"
    assert resp["daily"][0]["key"] == AGENT_ID


@pytest.mark.asyncio
async def test_usage_daily_rejects_bad_group_by():
    from fastapi import HTTPException

    from app.api.ai_library_router import get_usage_daily

    with pytest.raises(HTTPException) as exc:
        await get_usage_daily(_auth(), days=7, group_by="team")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_usage_daily_rejects_bad_days():
    from fastapi import HTTPException

    from app.api.ai_library_router import get_usage_daily

    with pytest.raises(HTTPException) as exc:
        await get_usage_daily(_auth(), days=0)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_usage_daily_month_mode_windows_calendar_month():
    """month=YYYY-MM overrides days: repo gets [month start, next month)."""
    from app.api.ai_library_router import get_usage_daily

    captured = {}

    async def _fake_daily(**kwargs):
        captured.update(kwargs)
        return []

    with patch("app.api.ai_library_router.get_agent_runs_repository") as repo_factory:
        repo = MagicMock()
        repo.daily_usage = AsyncMock(side_effect=_fake_daily)
        repo_factory.return_value = repo

        resp = await get_usage_daily(_auth(), days=30, month="2026-06")

    assert captured["started_after"].isoformat().startswith("2026-06-01")
    assert captured["started_before"].isoformat().startswith("2026-07-01")
    assert resp["month"] == "2026-06"


@pytest.mark.asyncio
async def test_usage_runs_month_mode_windows_calendar_month():
    from app.api.ai_library_router import get_usage_runs

    captured = {}

    async def _fake_list(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0}

    with patch("app.api.ai_library_router.get_agent_runs_repository") as repo_factory:
        repo = MagicMock()
        repo.list_runs_admin = AsyncMock(side_effect=_fake_list)
        repo_factory.return_value = repo

        await get_usage_runs(_auth(), month="2026-06")

    assert captured["started_after"].isoformat().startswith("2026-06-01")
    assert captured["started_before"].isoformat().startswith("2026-07-01")
