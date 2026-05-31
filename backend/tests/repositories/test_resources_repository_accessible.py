"""Verify list_accessible_for_user filters by ownership + team membership + q + kinds."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_scope_team_id_narrows_to_team_plus_personal():
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(
            user_id="user-1",
            scope_team_id="900123",
        )

    sql = captured["sql"].lower()
    # Still gated on membership (no escalation via a forged team_id)…
    assert "team_members" in sql
    # …but now narrowed to the passed team OR the caller's personal team.
    assert captured["params"]["scope_team_id"] == "900123"
    assert "kind = 'personal'" in sql


@pytest.mark.asyncio
async def test_scope_team_id_omitted_keeps_all_teams():
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(user_id="user-1")

    assert "scope_team_id" not in captured["params"]
    assert "team_members" in captured["sql"].lower()
