"""Tests for POST /api/v1/ai-library/agents (Phase 2 PR 2.8a).

Creates a user-owned (non-preset) agent. Optional fork_from copies
behavioral content from an existing agent. Covers happy path, slug
collision, fork-source-not-found, and explicit-override-wins-fork.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router


def _app_with_router() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_router())


@pytest.fixture
def fake_auth():
    class _FakeAuth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "user@example.com"

    return _FakeAuth()


def _install_auth_override(app: FastAPI, fake_auth) -> None:
    from app.core.deps import get_auth

    async def _grant():
        return fake_auth

    app.dependency_overrides[get_auth] = _grant


def _patch_agent_repo(inserted_rows: list, existing: Dict[str, Any] | None = None):
    """Helper: patch AgentRepository to return `existing` for get_by_slug
    and record inserts into `inserted_rows`."""
    from unittest.mock import patch as _patch

    repo_mock = AsyncMock()
    repo_mock.get_by_slug = AsyncMock(return_value=existing)

    async def _insert(fields: Dict[str, Any]) -> Dict[str, Any]:
        inserted_rows.append(fields)
        created = {
            "id": str(uuid4()),
            "created_at": "2026-04-21T00:00:00Z",
            "updated_at": "2026-04-21T00:00:00Z",
            **fields,
        }
        return created

    repo_mock.insert = AsyncMock(side_effect=_insert)

    return _patch(
        "app.api.ai_library_router._repos",
        return_value=(repo_mock, AsyncMock()),
    )


def test_create_agent_happy_path(client: TestClient, fake_auth) -> None:
    """POST with slug + name → 201 + user_id populated + is_system_preset=false."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with _patch_agent_repo(inserted, existing=None):
        resp = client.post(
            "/api/v1/ai-library/agents",
            json={"slug": "my-custom", "name": "My Custom"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "my-custom"
    assert body["name"] == "My Custom"
    assert body["is_system_preset"] is False
    assert body["user_id"] == fake_auth.user_id
    assert body["skill_ids"] == []

    # The insert payload had the expected defaults
    assert len(inserted) == 1
    row = inserted[0]
    assert row["is_system_preset"] is False
    assert row["user_id"] == fake_auth.user_id
    assert row["model"] == "qwen-max"  # default
    assert row["temperature"] == 0.7

    client.app.dependency_overrides.clear()


def test_create_agent_slug_collision_returns_409(client: TestClient, fake_auth) -> None:
    _install_auth_override(client.app, fake_auth)

    existing = {"id": str(uuid4()), "slug": "summarize", "name": "Summarize"}
    inserted: list = []
    with _patch_agent_repo(inserted, existing=existing):
        resp = client.post(
            "/api/v1/ai-library/agents",
            json={"slug": "summarize", "name": "My Summarize"},
        )

    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    assert inserted == []  # Nothing was created

    client.app.dependency_overrides.clear()


def test_create_agent_fork_from_copies_content(client: TestClient, fake_auth) -> None:
    """fork_from: copies identity/soul/agent_md + model/temp/max from source."""
    _install_auth_override(client.app, fake_auth)

    # Use a more elaborate patch to support sequential get_by_slug calls:
    # first call checks if new slug collides (None), second fetches fork source.
    from unittest.mock import patch as _patch

    repo_mock = AsyncMock()

    async def _flaky_get_by_slug(slug: str):
        if slug == "my-fork":
            return None  # no collision
        if slug == "summarize":
            return {
                "id": str(uuid4()),
                "slug": "summarize",
                "name": "Summarize",
                "identity_md": "FORKED IDENTITY",
                "soul_md": "FORKED SOUL",
                "agent_md": "FORKED AGENT",
                "model": "gpt-4o",
                "temperature": 0.3,
                "max_tokens": 2048,
            }
        return None

    repo_mock.get_by_slug = AsyncMock(side_effect=_flaky_get_by_slug)

    inserted_rows: list = []

    async def _insert(fields: Dict[str, Any]) -> Dict[str, Any]:
        inserted_rows.append(fields)
        return {
            "id": str(uuid4()),
            "created_at": "2026-04-21T00:00:00Z",
            "updated_at": "2026-04-21T00:00:00Z",
            **fields,
        }

    repo_mock.insert = AsyncMock(side_effect=_insert)

    with _patch(
        "app.api.ai_library_router._repos",
        return_value=(repo_mock, AsyncMock()),
    ):
        resp = client.post(
            "/api/v1/ai-library/agents",
            json={
                "slug": "my-fork",
                "name": "My Fork",
                "fork_from": "summarize",
            },
        )

    assert resp.status_code == 201, resp.text
    assert len(inserted_rows) == 1
    row = inserted_rows[0]
    assert row["identity_md"] == "FORKED IDENTITY"
    assert row["soul_md"] == "FORKED SOUL"
    assert row["agent_md"] == "FORKED AGENT"
    assert row["model"] == "gpt-4o"
    assert row["temperature"] == 0.3
    assert row["max_tokens"] == 2048

    client.app.dependency_overrides.clear()


def test_create_agent_fork_source_not_found_returns_404(
    client: TestClient, fake_auth
) -> None:
    _install_auth_override(client.app, fake_auth)

    from unittest.mock import patch as _patch

    repo_mock = AsyncMock()
    repo_mock.get_by_slug = AsyncMock(return_value=None)  # both lookups miss
    repo_mock.insert = AsyncMock()

    with _patch(
        "app.api.ai_library_router._repos",
        return_value=(repo_mock, AsyncMock()),
    ):
        resp = client.post(
            "/api/v1/ai-library/agents",
            json={
                "slug": "my-fork",
                "name": "My Fork",
                "fork_from": "does-not-exist",
            },
        )

    assert resp.status_code == 404
    assert "fork source" in resp.json()["detail"]
    repo_mock.insert.assert_not_awaited()

    client.app.dependency_overrides.clear()


def test_create_agent_explicit_override_wins_fork(
    client: TestClient, fake_auth
) -> None:
    """When both fork_from and explicit model are given, explicit wins."""
    _install_auth_override(client.app, fake_auth)

    from unittest.mock import patch as _patch

    repo_mock = AsyncMock()

    async def _flaky_get_by_slug(slug: str):
        if slug == "override-wins":
            return None
        if slug == "summarize":
            return {
                "id": str(uuid4()),
                "slug": "summarize",
                "model": "forked-model",
                "temperature": 0.3,
                "max_tokens": 2048,
                "identity_md": "forked",
                "soul_md": None,
                "agent_md": None,
            }
        return None

    repo_mock.get_by_slug = AsyncMock(side_effect=_flaky_get_by_slug)

    inserted_rows: list = []

    async def _insert(fields: Dict[str, Any]) -> Dict[str, Any]:
        inserted_rows.append(fields)
        return {
            "id": str(uuid4()),
            "created_at": "2026-04-21T00:00:00Z",
            "updated_at": "2026-04-21T00:00:00Z",
            **fields,
        }

    repo_mock.insert = AsyncMock(side_effect=_insert)

    with _patch(
        "app.api.ai_library_router._repos",
        return_value=(repo_mock, AsyncMock()),
    ):
        resp = client.post(
            "/api/v1/ai-library/agents",
            json={
                "slug": "override-wins",
                "name": "Override Wins",
                "fork_from": "summarize",
                "model": "claude-opus-4-5",
                "temperature": 0.9,
            },
        )

    assert resp.status_code == 201
    row = inserted_rows[0]
    # Explicit fields won
    assert row["model"] == "claude-opus-4-5"
    assert row["temperature"] == 0.9
    # Non-overridden forked fields stayed
    assert row["max_tokens"] == 2048
    assert row["identity_md"] == "forked"

    client.app.dependency_overrides.clear()


def test_create_agent_rejects_bad_slug_pattern(client: TestClient, fake_auth) -> None:
    """Slug must match ^[a-z0-9_-]+$ — uppercase / spaces / symbols rejected."""
    _install_auth_override(client.app, fake_auth)

    resp = client.post(
        "/api/v1/ai-library/agents",
        json={"slug": "Has Spaces!", "name": "Bad Slug"},
    )
    assert resp.status_code == 422  # Pydantic validation failure

    client.app.dependency_overrides.clear()
