"""Admin AI Usage endpoint: paginated per-user × per-model agent_runs with
email enrichment + duration derivation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.admin.ai_usage_router import (
    _duration_ms,
    _email_cache,
    _emails_for,
    list_ai_usage_runs,
)


@pytest.fixture(autouse=True)
def _clear_email_cache():
    """The email TTL cache is module-level — isolate tests from each other."""
    _email_cache.clear()
    yield
    _email_cache.clear()


def test_duration_ms_computes_from_iso_strings():
    assert (
        _duration_ms("2026-07-04T00:00:00+00:00", "2026-07-04T00:00:01.500000+00:00")
        == 1500
    )


def test_duration_ms_none_when_missing_or_negative():
    assert _duration_ms(None, "2026-07-04T00:00:01+00:00") is None
    assert _duration_ms("2026-07-04T00:00:01+00:00", None) is None
    # ended before started → None, not a negative number
    assert (
        _duration_ms("2026-07-04T00:00:05+00:00", "2026-07-04T00:00:01+00:00") is None
    )


def test_duration_ms_unparseable_is_none():
    assert _duration_ms("not-a-date", "also-not") is None


@pytest.mark.asyncio
async def test_list_runs_builds_items_and_enriches_email():
    """Rows from the repo are shaped into AiUsageRunItem, user_id resolved to
    email, cost/tokens coerced, duration derived."""
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    repo_rows = {
        "items": [
            {
                "id": 123,
                "user_id": "11111111-1111-1111-1111-111111111111",
                "agent_id": "22222222-2222-2222-2222-222222222222",
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "status": "completed",
                "trigger": "chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost_cents": "0.42",
                "started_at": "2026-07-04T00:00:00+00:00",
                "ended_at": "2026-07-04T00:00:02+00:00",
                "error_code": None,
            }
        ],
        "total": 1,
    }
    email_map = {
        "11111111-1111-1111-1111-111111111111": ("user@example.com", None),
    }

    with (
        patch(
            "app.api.admin.ai_usage_router.get_agent_runs_repository"
        ) as mock_repo_factory,
        patch(
            "app.api.admin.ai_usage_router.batch_get_user_auth_info",
            new=AsyncMock(return_value=email_map),
        ),
    ):
        mock_repo = MagicMock()
        mock_repo.list_runs_admin = AsyncMock(return_value=repo_rows)
        mock_repo_factory.return_value = mock_repo

        resp = await list_ai_usage_runs(
            fake_auth,
            page=1,
            page_size=20,
            user_id=None,
            model=None,
            provider=None,
            status=None,
            days=None,
            sort_by="started_at",
            sort_order="desc",
        )

    assert resp.total == 1
    assert resp.page == 1
    item = resp.items[0]
    assert item.id == "123"
    assert item.user_email == "user@example.com"
    assert item.model == "deepseek-v4-flash"
    assert item.cost_cents == 0.42
    assert item.total_tokens == 15
    assert item.duration_ms == 2000  # 2s window


@pytest.mark.asyncio
async def test_list_runs_rejects_bad_user_id():
    """A non-UUID user_id filter → 400, not a 500 downstream."""
    from fastapi import HTTPException

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    with pytest.raises(HTTPException) as exc:
        await list_ai_usage_runs(fake_auth, user_id="not-a-uuid")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_runs_passes_filters_to_repo():
    """model/status/sort forward to the repo; days becomes started_after."""
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    captured = {}

    async def _fake_list(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0}

    with (
        patch(
            "app.api.admin.ai_usage_router.get_agent_runs_repository"
        ) as mock_repo_factory,
        patch(
            "app.api.admin.ai_usage_router.batch_get_user_auth_info",
            new=AsyncMock(return_value={}),
        ),
    ):
        mock_repo = MagicMock()
        mock_repo.list_runs_admin = AsyncMock(side_effect=_fake_list)
        mock_repo_factory.return_value = mock_repo

        await list_ai_usage_runs(
            fake_auth,
            page=2,
            page_size=10,
            user_id=None,
            provider=None,
            model="deepseek-v4-pro",
            status="failed",
            days=7,
            sort_by="cost_cents",
            sort_order="asc",
        )

    assert captured["model"] == "deepseek-v4-pro"
    assert captured["status"] == "failed"
    assert captured["sort_by"] == "cost_cents"
    assert captured["sort_desc"] is False
    assert captured["offset"] == 10  # (page 2 - 1) * 10
    assert captured["limit"] == 10
    assert captured["started_after"] is not None


@pytest.mark.asyncio
async def test_summary_aggregates_daily_rows():
    """Summary sums requests/tokens/cost across the daily × model buckets."""
    from app.api.admin.ai_usage_router import ai_usage_summary

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    daily_rows = [
        {
            "date": "2026-07-01",
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
            "requests": 290,
            "total_tokens": 1_100_000,
            "cost_cents": "31.5",
        },
        {
            "date": "2026-07-01",
            "model": "doubao-seed-2-0-lite-260428",
            "provider": "doubao",
            "requests": 3,
            "total_tokens": 4_000,
            "cost_cents": "0.4",
        },
        {
            "date": "2026-07-02",
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
            "requests": 288,
            "total_tokens": 1_050_000,
            "cost_cents": "30.0",
        },
    ]

    with patch(
        "app.api.admin.ai_usage_router.get_agent_runs_repository"
    ) as mock_repo_factory:
        mock_repo = MagicMock()
        mock_repo.daily_usage_by_model = AsyncMock(return_value=daily_rows)
        mock_repo_factory.return_value = mock_repo

        resp = await ai_usage_summary(fake_auth, days=30)

    assert resp.days == 30
    assert resp.total_requests == 581
    assert resp.total_tokens == 2_154_000
    assert resp.total_cost_cents == pytest.approx(61.9)
    assert len(resp.daily) == 3
    assert resp.daily[0].model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_list_runs_skips_system_sentinel_in_email_enrich():
    """The all-zeros system user_id must never hit the auth-admin lookup."""
    from app.api.admin.ai_usage_router import SYSTEM_RUN_USER_ID

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    repo_rows = {
        "items": [
            {
                "id": 1,
                "user_id": SYSTEM_RUN_USER_ID,
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "status": "failed",
                "trigger": "topic_scorer",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_cents": 0,
                "started_at": "2026-07-04T00:00:00+00:00",
                "ended_at": None,
                "error_code": "HTTPStatusError",
            }
        ],
        "total": 1,
    }

    batch_mock = AsyncMock(return_value={})
    with (
        patch(
            "app.api.admin.ai_usage_router.get_agent_runs_repository"
        ) as mock_repo_factory,
        patch(
            "app.api.admin.ai_usage_router.batch_get_user_auth_info",
            new=batch_mock,
        ),
    ):
        mock_repo = MagicMock()
        mock_repo.list_runs_admin = AsyncMock(return_value=repo_rows)
        mock_repo_factory.return_value = mock_repo

        resp = await list_ai_usage_runs(
            fake_auth,
            page=1,
            page_size=20,
            user_id=None,
            model=None,
            provider=None,
            status=None,
            days=None,
            sort_by="started_at",
            sort_order="desc",
        )

    batch_mock.assert_not_awaited()  # sentinel never reaches auth admin
    assert resp.items[0].user_email is None
    assert resp.items[0].trigger == "topic_scorer"


@pytest.mark.asyncio
async def test_email_cache_only_fetches_misses():
    """Second resolution of the same user hits the TTL cache — no repeat
    auth-admin call (the table polls every 30s; emails are cached 10 min)."""
    calls = []

    async def _fake_batch(uids):
        calls.append(list(uids))
        return {uid: (f"{uid[:4]}@example.com", None) for uid in uids}

    with patch(
        "app.api.admin.ai_usage_router.batch_get_user_auth_info",
        new=AsyncMock(side_effect=_fake_batch),
    ):
        first = await _emails_for(["aaaa-1", "bbbb-2"])
        second = await _emails_for(["aaaa-1", "bbbb-2", "cccc-3"])

    assert first["aaaa-1"] == "aaaa@example.com"
    assert second["cccc-3"] == "cccc@example.com"
    # one call for the initial pair, one for the single new miss
    assert calls == [["aaaa-1", "bbbb-2"], ["cccc-3"]]
