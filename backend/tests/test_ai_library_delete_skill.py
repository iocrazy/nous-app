"""Tests for DELETE /api/v1/ai-library/skills/{slug}.

Authorization matrix:
    1. Owner can delete their user-owned skill           → 204
    2. Non-owner cannot delete someone else's skill      → 403
    3. Non-admin cannot delete a system preset           → 403
    4. Admin can delete a system preset                  → 204
    5. Unknown slug                                      → 404

Mirrors the hermetic patch style used in
``tests/test_ai_library_create_skill.py`` — we override ``get_auth``
and patch ``_repos`` + ``_user_is_admin`` instead of hitting a real DB.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router

OWNER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"
ADMIN_ID = "33333333-3333-3333-3333-333333333333"


def _app_with_router() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_router())


def _install_auth(app: FastAPI, user_id: str) -> None:
    from app.core.deps import get_auth

    class _FakeAuth:
        def __init__(self, uid: str) -> None:
            self.user_id = uid
            self.email = f"{uid[:8]}@example.com"

    async def _grant() -> _FakeAuth:
        return _FakeAuth(user_id)

    app.dependency_overrides[get_auth] = _grant


def _patch_skill_repo(
    *,
    skill: Optional[Dict[str, Any]],
    deleted: list,
):
    """Patch SkillRepository so ``get_by_slug`` returns ``skill`` and
    ``delete`` records the id into ``deleted``.
    """
    repo_mock = AsyncMock()

    async def _get_by_slug(slug: str) -> Optional[Dict[str, Any]]:
        if skill is not None and slug == skill.get("slug"):
            return skill
        return None

    async def _delete(skill_id: int) -> None:
        deleted.append(skill_id)

    repo_mock.get_by_slug = AsyncMock(side_effect=_get_by_slug)
    repo_mock.delete = AsyncMock(side_effect=_delete)

    return patch(
        "app.api.ai_library_router._repos",
        return_value=(AsyncMock(), repo_mock),
    )


def _patch_admin(is_admin: bool):
    return patch(
        "app.api.ai_library_router._user_is_admin",
        AsyncMock(return_value=is_admin),
    )


def _user_owned_skill(*, slug: str = "my-skill") -> Dict[str, Any]:
    return {
        "id": 42,
        "slug": slug,
        "name": "My Skill",
        "is_public": False,
        "created_by": OWNER_ID,
        "team_id": None,
        "project_id": None,
    }


def _system_preset_skill(*, slug: str = "script-outline") -> Dict[str, Any]:
    return {
        "id": 101,
        "slug": slug,
        "name": "Script Outline",
        "is_public": True,
        "created_by": None,
        "team_id": None,
        "project_id": None,
    }


def test_owner_can_delete_own_skill(client: TestClient) -> None:
    """Owner deletes a user-owned skill → 204, delete() called once."""
    _install_auth(client.app, OWNER_ID)
    deleted: list = []
    with _patch_skill_repo(skill=_user_owned_skill(), deleted=deleted):
        resp = client.delete("/api/v1/ai-library/skills/my-skill")
    assert resp.status_code == 204, resp.text
    assert resp.text == ""
    assert deleted == [42]
    client.app.dependency_overrides.clear()


def test_non_owner_cannot_delete_skill(client: TestClient) -> None:
    """Different user tries to delete someone's skill → 403, no delete."""
    _install_auth(client.app, OTHER_ID)
    deleted: list = []
    with _patch_skill_repo(skill=_user_owned_skill(), deleted=deleted):
        resp = client.delete("/api/v1/ai-library/skills/my-skill")
    assert resp.status_code == 403, resp.text
    assert "owner" in resp.json()["detail"].lower()
    assert deleted == []
    client.app.dependency_overrides.clear()


def test_non_admin_cannot_delete_system_preset(client: TestClient) -> None:
    """Regular user tries to delete a system preset → 403, no delete."""
    _install_auth(client.app, OTHER_ID)
    deleted: list = []
    with (
        _patch_skill_repo(skill=_system_preset_skill(), deleted=deleted),
        _patch_admin(is_admin=False),
    ):
        resp = client.delete("/api/v1/ai-library/skills/script-outline")
    assert resp.status_code == 403, resp.text
    assert "admin" in resp.json()["detail"].lower()
    assert deleted == []
    client.app.dependency_overrides.clear()


def test_admin_can_delete_system_preset(client: TestClient) -> None:
    """Admin deletes a system preset → 204, delete() called."""
    _install_auth(client.app, ADMIN_ID)
    deleted: list = []
    with (
        _patch_skill_repo(skill=_system_preset_skill(), deleted=deleted),
        _patch_admin(is_admin=True),
    ):
        resp = client.delete("/api/v1/ai-library/skills/script-outline")
    assert resp.status_code == 204, resp.text
    assert deleted == [101]
    client.app.dependency_overrides.clear()


def test_delete_unknown_slug_returns_404(client: TestClient) -> None:
    """Nonexistent slug → 404."""
    _install_auth(client.app, OWNER_ID)
    deleted: list = []
    with _patch_skill_repo(skill=None, deleted=deleted):
        resp = client.delete("/api/v1/ai-library/skills/does-not-exist")
    assert resp.status_code == 404, resp.text
    assert deleted == []
    client.app.dependency_overrides.clear()
