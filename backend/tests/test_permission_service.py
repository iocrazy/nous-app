"""Unit tests for PermissionService.

Uses an in-memory PermissionRepository fake to exercise the ReBAC
resolution algorithm without touching Supabase.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services.library.permission_service import (
    CAPABILITIES,
    PermissionService,
    TEAM_ROLE_MAP,
)


class _FakePermissionRepository:
    """In-memory stand-in for PermissionRepository."""

    def __init__(self) -> None:
        self.overrides: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.folders: dict[str, dict[str, Any]] = {}
        self.libraries: dict[str, dict[str, Any]] = {}
        self.resource_scopes: dict[str, dict[str, Any]] = {}
        self.team_roles: dict[tuple[str, str], str] = {}

    async def get_access_override(
        self, object_type: str, object_id: str, user_id: str
    ) -> Optional[dict[str, Any]]:
        return self.overrides.get((object_type, object_id, user_id))

    async def get_folder_by_id(self, folder_id: str) -> Optional[dict[str, Any]]:
        return self.folders.get(folder_id)

    async def get_library_by_id(self, library_id: str) -> Optional[dict[str, Any]]:
        return self.libraries.get(library_id)

    async def get_resource_item_scope(
        self, resource_id: str
    ) -> Optional[dict[str, Any]]:
        return self.resource_scopes.get(resource_id)

    async def get_team_member_role(
        self, user_id: str, team_id: str
    ) -> Optional[str]:
        return self.team_roles.get((user_id, team_id))


@pytest.fixture
def fake_repo() -> _FakePermissionRepository:
    return _FakePermissionRepository()


@pytest.fixture
def service(fake_repo: _FakePermissionRepository) -> PermissionService:
    svc = PermissionService()
    svc._repo = fake_repo  # type: ignore[assignment]
    return svc


# ─── Capabilities & mappings ───────────────────────────────────────


def test_capabilities_admin_has_manage() -> None:
    assert "manage" in CAPABILITIES["admin"]
    assert "delete" in CAPABILITIES["admin"]


def test_capabilities_viewer_cannot_modify() -> None:
    assert CAPABILITIES["viewer"] == ["view", "download"]


def test_capabilities_none_is_empty() -> None:
    assert CAPABILITIES["none"] == []


def test_team_role_map_owner_to_admin() -> None:
    assert TEAM_ROLE_MAP["owner"] == "admin"
    assert TEAM_ROLE_MAP["admin"] == "admin"
    assert TEAM_ROLE_MAP["member"] == "editor"


# ─── Direct override ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_direct_override_wins(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.overrides[("folder", "f-1", "u-1")] = {"role": "admin"}
    result = await service.get_effective_role("u-1", "folder", "f-1", "t-1")
    assert result["role"] == "admin"
    assert "manage" in result["capabilities"]


@pytest.mark.asyncio
async def test_override_viewer_returns_view_only(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.overrides[("resource", "r-1", "u-1")] = {"role": "viewer"}
    result = await service.get_effective_role("u-1", "resource", "r-1", "t-1")
    assert result["role"] == "viewer"
    assert result["capabilities"] == ["view", "download"]


# ─── Team role fallback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_to_team_owner(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.team_roles[("u-1", "t-1")] = "owner"
    result = await service.get_effective_role("u-1", "unknown_type", "x", "t-1")
    assert result["role"] == "admin"


@pytest.mark.asyncio
async def test_non_member_gets_none(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    # No team_roles entry → user is not a member
    result = await service.get_effective_role("u-1", "unknown_type", "x", "t-1")
    assert result["role"] == "none"
    assert result["capabilities"] == []


@pytest.mark.asyncio
async def test_member_becomes_editor(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.team_roles[("u-1", "t-1")] = "member"
    result = await service.get_effective_role("u-1", "unknown_type", "x", "t-1")
    assert result["role"] == "editor"


# ─── Folder hierarchy ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_folder_missing_falls_back_to_team(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.team_roles[("u-1", "t-1")] = "admin"
    result = await service.get_effective_role("u-1", "folder", "missing", "t-1")
    assert result["role"] == "admin"


@pytest.mark.asyncio
async def test_parent_folder_override_is_inherited(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.folders["f-child"] = {"id": "f-child", "parent_id": "f-parent"}
    fake_repo.folders["f-parent"] = {"id": "f-parent", "parent_id": None}
    fake_repo.overrides[("folder", "f-parent", "u-1")] = {"role": "editor"}
    fake_repo.team_roles[("u-1", "t-1")] = "member"

    result = await service.get_effective_role("u-1", "folder", "f-child", "t-1")
    assert result["role"] == "editor"


@pytest.mark.asyncio
async def test_folder_with_no_parent_uses_team_role(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.folders["f-1"] = {"id": "f-1", "parent_id": None}
    fake_repo.team_roles[("u-1", "t-1")] = "owner"

    result = await service.get_effective_role("u-1", "folder", "f-1", "t-1")
    assert result["role"] == "admin"


# ─── Library visibility ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restricted_library_returns_none_without_override(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.libraries["lib-1"] = {"id": "lib-1", "visibility": "restricted"}
    fake_repo.team_roles[("u-1", "t-1")] = "member"

    result = await service.get_effective_role("u-1", "library", "lib-1", "t-1")
    assert result["role"] == "none"


@pytest.mark.asyncio
async def test_restricted_library_with_override_uses_override(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.libraries["lib-1"] = {"id": "lib-1", "visibility": "restricted"}
    fake_repo.overrides[("library", "lib-1", "u-1")] = {"role": "editor"}
    fake_repo.team_roles[("u-1", "t-1")] = "member"

    result = await service.get_effective_role("u-1", "library", "lib-1", "t-1")
    assert result["role"] == "editor"


@pytest.mark.asyncio
async def test_public_library_uses_team_role(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.libraries["lib-1"] = {"id": "lib-1", "visibility": "public"}
    fake_repo.team_roles[("u-1", "t-1")] = "owner"

    result = await service.get_effective_role("u-1", "library", "lib-1", "t-1")
    assert result["role"] == "admin"


# ─── Resource scope ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resource_inherits_folder_override(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.resource_scopes["r-1"] = {"folder_id": "f-1"}
    fake_repo.overrides[("folder", "f-1", "u-1")] = {"role": "viewer"}
    fake_repo.team_roles[("u-1", "t-1")] = "member"

    result = await service.get_effective_role("u-1", "resource", "r-1", "t-1")
    assert result["role"] == "viewer"


@pytest.mark.asyncio
async def test_resource_without_folder_uses_team_role(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    fake_repo.resource_scopes["r-1"] = {"folder_id": None}
    fake_repo.team_roles[("u-1", "t-1")] = "member"

    result = await service.get_effective_role("u-1", "resource", "r-1", "t-1")
    assert result["role"] == "editor"


@pytest.mark.asyncio
async def test_resource_missing_scope_uses_team_role(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    # No scope entry for this resource
    fake_repo.team_roles[("u-1", "t-1")] = "owner"

    result = await service.get_effective_role("u-1", "resource", "unknown", "t-1")
    assert result["role"] == "admin"


# ─── Error handling ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repo_exception_falls_back_to_viewer(
    service: PermissionService, fake_repo: _FakePermissionRepository
) -> None:
    async def _raises(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("db down")

    fake_repo.get_access_override = _raises  # type: ignore[method-assign]

    result = await service.get_effective_role("u-1", "folder", "f-1", "t-1")
    assert result["role"] == "viewer"
    assert result["capabilities"] == ["view", "download"]
