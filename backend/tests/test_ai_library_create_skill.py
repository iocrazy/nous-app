"""Tests for POST /api/v1/ai-library/skills (Phase 2 skills create flow).

Mirrors test_ai_library_create_agent.py. Creates a user-owned
(non-preset) skill. Optional ``fork_from`` copies body/metadata from
an existing skill. Scope via ``team_id`` / ``project_id`` is mutually
exclusive and requires membership.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch
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
        user_id = "22222222-2222-2222-2222-222222222222"
        email = "skill-user@example.com"

    return _FakeAuth()


def _install_auth_override(app: FastAPI, fake_auth) -> None:
    from app.core.deps import get_auth

    async def _grant():
        return fake_auth

    app.dependency_overrides[get_auth] = _grant


def _patch_skill_repo(
    inserted_rows: list,
    existing: Optional[Dict[str, Any]] = None,
    *,
    fork_source: Optional[Dict[str, Any]] = None,
):
    """Patch SkillRepository so ``get_by_slug`` returns ``existing`` for the
    new slug and ``fork_source`` for the fork-from slug. Records inserts into
    ``inserted_rows``.
    """
    repo_mock = AsyncMock()

    async def _get_by_slug(slug: str):
        # First call is the collision check — return existing. Subsequent
        # calls for different slugs come from fork_from lookups.
        if existing is not None and slug == existing.get("slug"):
            return existing
        if fork_source is not None and slug == fork_source.get("slug"):
            return fork_source
        return None

    repo_mock.get_by_slug = AsyncMock(side_effect=_get_by_slug)

    async def _insert(fields: Dict[str, Any]) -> Dict[str, Any]:
        inserted_rows.append(fields)
        # Return a row that looks like Postgres would return. SkillOut expects
        # id (int), updated_at (str), files[] (filled by router).
        return {
            "id": 10_000 + len(inserted_rows),
            "updated_at": "2026-04-21T00:00:00Z",
            **fields,
        }

    repo_mock.insert = AsyncMock(side_effect=_insert)
    repo_mock.list_files = AsyncMock(return_value=[])

    return patch(
        "app.api.ai_library_router._repos",
        return_value=(AsyncMock(), repo_mock),
    )


def _patch_scope_helpers(
    *,
    is_team_member: bool = True,
    can_write_project: bool = True,
):
    """Patch the scope-membership helpers in ai_library_router."""
    return patch.multiple(
        "app.api.ai_library_router",
        _user_is_team_member=AsyncMock(return_value=is_team_member),
        _user_can_write_project=AsyncMock(return_value=can_write_project),
    )


def test_create_skill_private_succeeds(client: TestClient, fake_auth) -> None:
    """POST with slug + name → 201 + is_public=false + created_by populated."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with _patch_skill_repo(inserted, existing=None):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={"slug": "my-skill", "name": "My Skill"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "my-skill"
    assert body["name"] == "My Skill"
    assert body["is_public"] is False
    assert body["team_id"] is None
    assert body["project_id"] is None
    assert body["files"] == []

    assert len(inserted) == 1
    row = inserted[0]
    assert row["is_public"] is False
    assert row["status"] == "active"
    assert row["created_by"] == fake_auth.user_id
    assert row["team_id"] is None
    assert row["project_id"] is None

    client.app.dependency_overrides.clear()


def test_create_skill_with_team_id_member_succeeds(
    client: TestClient, fake_auth
) -> None:
    """User is a team member → skill is created with team_id populated."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with (
        _patch_skill_repo(inserted, existing=None),
        _patch_scope_helpers(is_team_member=True),
    ):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={"slug": "team-skill", "name": "Team Skill", "team_id": 42},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["team_id"] == 42
    assert body["project_id"] is None
    row = inserted[0]
    assert row["team_id"] == 42
    assert row["created_by"] == fake_auth.user_id

    client.app.dependency_overrides.clear()


def test_create_skill_with_team_id_non_member_forbidden(
    client: TestClient, fake_auth
) -> None:
    """User is NOT a team member → 403, no insert."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with (
        _patch_skill_repo(inserted, existing=None),
        _patch_scope_helpers(is_team_member=False),
    ):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={"slug": "team-skill", "name": "Team Skill", "team_id": 999},
        )

    assert resp.status_code == 403
    assert "not a member" in resp.json()["detail"]
    assert inserted == []

    client.app.dependency_overrides.clear()


def test_create_skill_with_project_id_succeeds(client: TestClient, fake_auth) -> None:
    """User owns / is a member of the project → skill created."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with (
        _patch_skill_repo(inserted, existing=None),
        _patch_scope_helpers(can_write_project=True),
    ):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={"slug": "proj-skill", "name": "Project Skill", "project_id": 7},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == 7
    assert body["team_id"] is None
    row = inserted[0]
    assert row["project_id"] == 7

    client.app.dependency_overrides.clear()


def test_create_skill_with_project_id_non_member_forbidden(
    client: TestClient, fake_auth
) -> None:
    """No project access → 403."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with (
        _patch_skill_repo(inserted, existing=None),
        _patch_scope_helpers(can_write_project=False),
    ):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={"slug": "proj-skill", "name": "Project Skill", "project_id": 123},
        )

    assert resp.status_code == 403
    assert inserted == []

    client.app.dependency_overrides.clear()


def test_create_skill_duplicate_slug_409(client: TestClient, fake_auth) -> None:
    _install_auth_override(client.app, fake_auth)

    existing = {"id": 1, "slug": "translate", "name": "Translate"}
    inserted: list = []
    with _patch_skill_repo(inserted, existing=existing):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={"slug": "translate", "name": "My Translate"},
        )

    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    assert inserted == []

    client.app.dependency_overrides.clear()


def test_create_skill_fork_copies_content(client: TestClient, fake_auth) -> None:
    """fork_from: copies body_md / frontmatter_json / category / icon /
    output_format / description from source."""
    _install_auth_override(client.app, fake_auth)

    fork_source = {
        "id": 7,
        "slug": "translate",
        "name": "Translate",
        "body_md": "FORKED BODY",
        "frontmatter_json": {"hint": "forked"},
        "category": "forked-cat",
        "icon": "🌐",
        "output_format": "markdown",
        "description": "forked description",
    }
    inserted: list = []
    with _patch_skill_repo(inserted, existing=None, fork_source=fork_source):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={
                "slug": "my-fork",
                "name": "My Fork",
                "fork_from": "translate",
            },
        )

    assert resp.status_code == 201, resp.text
    assert len(inserted) == 1
    row = inserted[0]
    assert row["body_md"] == "FORKED BODY"
    assert row["frontmatter_json"] == {"hint": "forked"}
    assert row["category"] == "forked-cat"
    assert row["icon"] == "🌐"
    assert row["output_format"] == "markdown"
    assert row["description"] == "forked description"
    # But ownership + publicity come from the creator, not the source.
    assert row["is_public"] is False
    assert row["created_by"] == fake_auth.user_id

    client.app.dependency_overrides.clear()


def test_create_skill_fork_source_not_found_returns_404(
    client: TestClient, fake_auth
) -> None:
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with _patch_skill_repo(inserted, existing=None, fork_source=None):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={
                "slug": "my-fork",
                "name": "My Fork",
                "fork_from": "does-not-exist",
            },
        )

    assert resp.status_code == 404
    assert "fork source" in resp.json()["detail"]
    assert inserted == []

    client.app.dependency_overrides.clear()


def test_create_skill_team_and_project_mutually_exclusive(
    client: TestClient, fake_auth
) -> None:
    """team_id + project_id both set → 422 (Pydantic model_validator)."""
    _install_auth_override(client.app, fake_auth)

    inserted: list = []
    with _patch_skill_repo(inserted, existing=None), _patch_scope_helpers():
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={
                "slug": "both-scopes",
                "name": "Both",
                "team_id": 1,
                "project_id": 2,
            },
        )

    # Pydantic model_validator rejects with 422; the payload never reaches the
    # route handler, so no insert happened.
    assert resp.status_code == 422
    assert "mutually exclusive" in resp.text
    assert inserted == []

    client.app.dependency_overrides.clear()


def test_create_skill_rejects_bad_slug_pattern(client: TestClient, fake_auth) -> None:
    """Slug must match ^[a-z0-9_-]+$."""
    _install_auth_override(client.app, fake_auth)

    resp = client.post(
        "/api/v1/ai-library/skills",
        json={"slug": "Has Spaces!", "name": "Bad"},
    )
    assert resp.status_code == 422

    client.app.dependency_overrides.clear()


def test_create_skill_explicit_override_wins_fork(
    client: TestClient, fake_auth
) -> None:
    """Explicit payload fields override forked values."""
    _install_auth_override(client.app, fake_auth)

    fork_source = {
        "id": 9,
        "slug": "translate",
        "body_md": "forked body",
        "category": "forked-cat",
        "icon": "🌐",
        "output_format": "markdown",
    }
    inserted: list = []
    with _patch_skill_repo(inserted, existing=None, fork_source=fork_source):
        resp = client.post(
            "/api/v1/ai-library/skills",
            json={
                "slug": "override-wins",
                "name": "Override Wins",
                "fork_from": "translate",
                "body_md": "MY BODY",
                "icon": "✨",
            },
        )

    assert resp.status_code == 201, resp.text
    row = inserted[0]
    # Explicit fields won
    assert row["body_md"] == "MY BODY"
    assert row["icon"] == "✨"
    # Non-overridden forked fields stayed
    assert row["category"] == "forked-cat"
    assert row["output_format"] == "markdown"

    client.app.dependency_overrides.clear()


def _uuid_str() -> str:
    return str(uuid4())
