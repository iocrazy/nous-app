"""Admin memory stats endpoint tests (Phase C2 observability).

Tests for:
  GET /admin/settings/memory/stats

in ``app.api.admin.settings_router``.

Pattern mirrors test_admin_memory_promotions.py: patch the repo function at
the import site in the router module, pass a MagicMock auth with ``.user_id``,
call the handler directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_STATS_DICT = {
    "total_active": 12,
    "by_visibility": {"private": 8, "shared": 4},
    "by_scope": {"agent_user": 7, "team": 3, "project": 2},
    "by_status": {"active": 12, "archived": 2, "superseded": 1},
    "created_24h": 5,
    "created_7d": 10,
    "last_created_at": "2026-06-20T08:30:00",
    "promotions": {"pending": 3, "approved": 1, "rejected": 0},
}

_ZERO_STATS = {
    "total_active": 0,
    "by_visibility": {},
    "by_scope": {},
    "by_status": {},
    "created_24h": 0,
    "created_7d": 0,
    "last_created_at": None,
    "promotions": {"pending": 0, "approved": 0, "rejected": 0},
}


def _make_auth(user_id: str = "admin-uuid-1") -> MagicMock:
    auth = MagicMock()
    auth.user_id = user_id
    return auth


class _FakeSettings:
    """Minimal settings stand-in with the flag attribute only."""

    def __init__(self, flag: bool) -> None:
        self.FEATURE_AGENT_MEMORY = flag


# ---------------------------------------------------------------------------
# T1: recall_enabled reflects FEATURE_AGENT_MEMORY flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_stats_recall_enabled_true():
    """recall_enabled=True when FEATURE_AGENT_MEMORY is truthy."""
    from app.api.admin.settings_router import memory_stats

    auth = _make_auth()
    mock_get = AsyncMock(return_value=_STATS_DICT)

    with (
        patch("app.api.admin.settings_router.get_memory_stats", mock_get),
        patch("app.api.admin.settings_router.settings", _FakeSettings(flag=True)),
    ):
        result = await memory_stats(auth=auth)

    assert result.recall_enabled is True


@pytest.mark.asyncio
async def test_memory_stats_recall_enabled_false():
    """recall_enabled=False when FEATURE_AGENT_MEMORY is falsy."""
    from app.api.admin.settings_router import memory_stats

    auth = _make_auth()
    mock_get = AsyncMock(return_value=_STATS_DICT)

    with (
        patch("app.api.admin.settings_router.get_memory_stats", mock_get),
        patch("app.api.admin.settings_router.settings", _FakeSettings(flag=False)),
    ):
        result = await memory_stats(auth=auth)

    assert result.recall_enabled is False


# ---------------------------------------------------------------------------
# T2: stats dict values pass through to MemoryStatsResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_stats_counts_pass_through():
    """All count fields from the repo dict are reflected in the response."""
    from app.api.admin.settings_router import memory_stats

    auth = _make_auth()
    mock_get = AsyncMock(return_value=_STATS_DICT)

    with (
        patch("app.api.admin.settings_router.get_memory_stats", mock_get),
        patch("app.api.admin.settings_router.settings", _FakeSettings(flag=True)),
    ):
        result = await memory_stats(auth=auth)

    assert result.total_active == 12
    assert result.by_visibility == {"private": 8, "shared": 4}
    assert result.by_scope == {"agent_user": 7, "team": 3, "project": 2}
    assert result.by_status == {"active": 12, "archived": 2, "superseded": 1}
    assert result.created_24h == 5
    assert result.created_7d == 10
    assert result.last_created_at == "2026-06-20T08:30:00"
    assert result.promotions == {"pending": 3, "approved": 1, "rejected": 0}


@pytest.mark.asyncio
async def test_memory_stats_zero_dict_yields_zero_response():
    """Zero/empty stats dict (error fallback) yields zeroed response fields."""
    from app.api.admin.settings_router import memory_stats

    auth = _make_auth()
    mock_get = AsyncMock(return_value=_ZERO_STATS)

    with (
        patch("app.api.admin.settings_router.get_memory_stats", mock_get),
        patch("app.api.admin.settings_router.settings", _FakeSettings(flag=False)),
    ):
        result = await memory_stats(auth=auth)

    assert result.total_active == 0
    assert result.by_visibility == {}
    assert result.last_created_at is None
    assert result.promotions == {"pending": 0, "approved": 0, "rejected": 0}


# ---------------------------------------------------------------------------
# T3: get_memory_stats is awaited (called once per request)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_stats_calls_repo_once():
    """The endpoint calls get_memory_stats exactly once per request."""
    from app.api.admin.settings_router import memory_stats

    auth = _make_auth()
    mock_get = AsyncMock(return_value=_STATS_DICT)

    with (
        patch("app.api.admin.settings_router.get_memory_stats", mock_get),
        patch("app.api.admin.settings_router.settings", _FakeSettings(flag=True)),
    ):
        await memory_stats(auth=auth)

    mock_get.assert_awaited_once_with()
