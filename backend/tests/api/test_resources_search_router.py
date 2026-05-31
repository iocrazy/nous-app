"""Smoke-test the search router shape + permission gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.main import app

client = TestClient(app)


def test_search_requires_auth():
    r = client.get("/api/v1/resources/search?q=story")
    assert r.status_code in (401, 403)


def test_search_returns_results_and_counts():
    fake_rows = [
        {
            "id": "1",
            "name": "story.md",
            "mime": "text/markdown",
            "size": 100,
            "scope_type": "personal",
            "scope_id": "u",
            "updated_at": "2026-05-24T10:00:00Z",
        },
    ]

    async def _fake_list(self, **kwargs):
        return fake_rows

    def _fake_auth() -> AuthContext:
        return AuthContext(user_id="u", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    try:
        with patch(
            "app.repositories.resources_repository.ResourcesRepository.list_accessible_for_user",
            new=_fake_list,
        ):
            r = client.get("/api/v1/resources/search?q=story&limit=5")
    finally:
        app.dependency_overrides.pop(get_auth, None)

    assert r.status_code == 200
    body = r.json()
    assert "results" in body and "counts" in body
    assert body["results"][0]["name"] == "story.md"


def test_search_forwards_team_id_to_repo():
    captured = {}

    async def _fake(self, **kwargs):
        captured.update(kwargs)
        return []

    def _fake_auth() -> AuthContext:
        return AuthContext(user_id="u", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    try:
        with patch(
            "app.repositories.resources_repository.ResourcesRepository.list_accessible_for_user",
            new=_fake,
        ):
            r = client.get("/api/v1/resources/search?q=story&team_id=900123")
            assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_auth, None)

    assert captured.get("scope_team_id") == "900123"


def test_search_without_team_id_passes_none():
    captured = {}

    async def _fake(self, **kwargs):
        captured.update(kwargs)
        return []

    def _fake_auth() -> AuthContext:
        return AuthContext(user_id="u", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    try:
        with patch(
            "app.repositories.resources_repository.ResourcesRepository.list_accessible_for_user",
            new=_fake,
        ):
            r = client.get("/api/v1/resources/search?q=story")
            assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_auth, None)

    assert captured.get("scope_team_id") is None
