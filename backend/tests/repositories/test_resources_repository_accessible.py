"""Verify list_accessible_for_user filters by ownership + team membership + q + kinds."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.repositories.resources_repository import ResourcesRepository


@pytest.mark.asyncio
async def test_accepts_q_and_kinds_and_limit(monkeypatch):
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(
            user_id="user-1",
            q="story",
            kinds=["video", "image"],
            limit=20,
        )

    assert "ilike" in captured["sql"].lower()
    assert captured["params"]["user_id"] == "user-1"
    assert "story" in captured["params"].get("q_like", "")
    # kinds_re is the actual SQL parameter used (kinds was a dead/orphan key, removed in S4 fix)
    assert "^video/" in captured["params"]["kinds_re"]
    assert "^image/" in captured["params"]["kinds_re"]
    assert captured["params"]["limit"] == 20


@pytest.mark.asyncio
async def test_default_limit_is_20_and_caps_at_50(monkeypatch):
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(user_id="u", limit=999)

    assert captured["params"]["limit"] == 50  # capped
