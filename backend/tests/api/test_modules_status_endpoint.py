"""Tests for GET /modules/status (batch module switches for the frontend).

Direct-function-call pattern (no TestClient fixture in this codebase),
mirroring tests/test_admin_modules_endpoint.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_status_returns_all_registered_modules():
    from app.api.modules_router import get_modules_status
    from app.core.cache import modules_status_cache

    modules_status_cache.invalidate("all")
    fake_auth = MagicMock()
    fake_auth.user_id = "user-1"

    with patch(
        "app.services.modules.registry._read_raw", new=AsyncMock(return_value=None)
    ):
        resp = await get_modules_status(fake_auth)

    by_id = {m.id: m for m in resp.modules}
    # all nine registered modules present
    assert set(by_id) == {
        "topic-inspiration",
        "distribution",
        "unified-storage",
        "media-parser",
        "projects",
        "shares",
        "todolist",
        "ai-library",
        "ideation",
    }
    # registry defaults flow through untouched
    assert by_id["shares"].enabled is True and by_id["shares"].visible is True
    assert by_id["distribution"].enabled is False


@pytest.mark.asyncio
async def test_status_is_cached_for_60s():
    from app.api.modules_router import get_modules_status
    from app.core.cache import modules_status_cache

    modules_status_cache.invalidate("all")
    fake_auth = MagicMock()
    fake_auth.user_id = "user-1"

    read = AsyncMock(return_value=None)
    with patch("app.services.modules.registry._read_raw", new=read):
        await get_modules_status(fake_auth)
        first_calls = read.await_count
        await get_modules_status(fake_auth)
    # second request served from cache — no extra registry reads
    assert read.await_count == first_calls
