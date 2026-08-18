"""Smoke-test the search router shape + permission gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_active_ai_tasks(monkeypatch):
    """Task 1b added a task_tracking lookup behind the resource AI status
    (the columns themselves only ever hold terminal values). These tests
    predate it and are about a different query, so stub it to "nothing
    running" rather than teach every fake session a second shape. The
    lookup itself is pinned in tests/services/ai/test_resource_ai_status.py.
    """
    import app.services.ai.resource_ai_status as status_module

    async def _none(resource_ids):
        return {}

    monkeypatch.setattr(status_module, "_active_ai_tasks", _none)


@pytest.fixture(autouse=True)
def _stub_counts(monkeypatch):
    """Tab badges moved to their own aggregate query (it reads the whole
    visible set, not the returned page), which needs a DB these tests do not
    have. Stub it here — the counts contract is pinned in
    test_resources_search_counts.py."""
    from app.repositories.resources_repository import ResourcesRepository

    async def _zero(self, **kwargs):
        return {"all": 0, "video": 0, "image": 0, "doc": 0, "audio": 0, "pdf": 0}

    monkeypatch.setattr(ResourcesRepository, "count_accessible_by_kind_for_user", _zero)


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


def test_search_empty_team_id_coerced_to_none():
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
            r = client.get("/api/v1/resources/search?q=story&team_id=")
            assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_auth, None)

    assert captured.get("scope_team_id") is None
