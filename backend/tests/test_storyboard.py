"""Integration tests for the Storyboard API endpoints.

Tests cover project CRUD, character CRUD, canvas sync, and export trigger.
All Supabase / service calls are mocked — we test the HTTP contract
(status codes, response envelope shape) not internal implementation.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = "/api/v1/storyboard"
FAKE_USER_ID = "user-test-1234"
FAKE_TEAM_ID = "team-test-5678"
FAKE_PROJECT_ID = "proj-001"
FAKE_CHAR_ID = "char-001"
FAKE_NODE_ID = "node-001"


# ---------------------------------------------------------------------------
# Auth override
# ---------------------------------------------------------------------------


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    """Bypass real auth for every test in this module."""
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_PROJECT = {
    "id": FAKE_PROJECT_ID,
    "name": "Test SB Project",
    "description": "desc",
    "status": "active",
    "created_by": FAKE_USER_ID,
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00",
}


def _svc_patch(method: str, return_value=None):
    """Shortcut to patch a StoryboardService async method."""
    target = f"app.services.storyboard_service.StoryboardService.{method}"
    return patch(target, new_callable=AsyncMock, return_value=return_value)


def _require_team_patch():
    return patch(
        "app.api.sb_projects_router.require_team_id",
        new_callable=AsyncMock,
        return_value=FAKE_TEAM_ID,
    )


# ===================================================================
# Project CRUD
# ===================================================================


class TestProjectCRUD:
    """POST / GET / PUT / DELETE on /storyboard/projects."""

    @pytest.mark.asyncio
    async def test_create_project(self, client: AsyncClient):
        with _require_team_patch(), _svc_patch("create_project", _FAKE_PROJECT):
            resp = await client.post(
                f"{BASE}/projects",
                json={"name": "Test SB Project", "description": "desc"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == FAKE_PROJECT_ID

    @pytest.mark.asyncio
    async def test_create_project_validation_empty_name(self, client: AsyncClient):
        """Name is required and min_length=1."""
        resp = await client.post(
            f"{BASE}/projects",
            json={"name": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_projects(self, client: AsyncClient):
        fake_result = {"items": [_FAKE_PROJECT], "total": 1, "page": 1, "limit": 20}
        with _require_team_patch(), _svc_patch("list_projects", fake_result):
            resp = await client.get(f"{BASE}/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "items" in body["data"]

    @pytest.mark.asyncio
    async def test_list_projects_pagination(self, client: AsyncClient):
        fake_result = {"items": [], "total": 0, "page": 2, "limit": 5}
        with _require_team_patch(), _svc_patch("list_projects", fake_result):
            resp = await client.get(f"{BASE}/projects?page=2&limit=5")
        assert resp.status_code == 200
        assert resp.json()["data"]["page"] == 2

    @pytest.mark.asyncio
    async def test_get_project(self, client: AsyncClient):
        with (
            _svc_patch("verify_project_access"),
            _svc_patch("get_project_full", _FAKE_PROJECT),
        ):
            resp = await client.get(f"{BASE}/projects/{FAKE_PROJECT_ID}")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Test SB Project"

    @pytest.mark.asyncio
    async def test_get_project_not_found(self, client: AsyncClient):
        with (
            _svc_patch("verify_project_access"),
            _svc_patch("get_project_full", None),
        ):
            resp = await client.get(f"{BASE}/projects/{FAKE_PROJECT_ID}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_project(self, client: AsyncClient):
        updated = {**_FAKE_PROJECT, "name": "Renamed"}
        with _svc_patch("verify_project_access"), _svc_patch("update_project", updated):
            resp = await client.put(
                f"{BASE}/projects/{FAKE_PROJECT_ID}",
                json={"name": "Renamed"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_delete_project(self, client: AsyncClient):
        with (
            _svc_patch("verify_project_access"),
            _svc_patch("soft_delete_project"),
        ):
            resp = await client.delete(f"{BASE}/projects/{FAKE_PROJECT_ID}")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ===================================================================
# Character CRUD
# ===================================================================


_FAKE_CHAR = {
    "id": FAKE_CHAR_ID,
    "project_id": FAKE_PROJECT_ID,
    "name": "Hero",
    "description": "Main character",
    "visual_traits": {"hair": "black"},
}


class TestCharacterCRUD:
    """POST / GET / PUT / DELETE on character endpoints."""

    @pytest.mark.asyncio
    async def test_create_character(self, client: AsyncClient):
        with (
            _svc_patch("verify_project_access"),
            _svc_patch("create_character", _FAKE_CHAR),
        ):
            resp = await client.post(
                f"{BASE}/projects/{FAKE_PROJECT_ID}/characters",
                json={"name": "Hero", "description": "Main character"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Hero"

    @pytest.mark.asyncio
    async def test_create_character_validation(self, client: AsyncClient):
        resp = await client.post(
            f"{BASE}/projects/{FAKE_PROJECT_ID}/characters",
            json={"name": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_characters(self, client: AsyncClient):
        with _svc_patch("verify_project_access"):
            with patch(
                "app.api.sb_characters_router.StoryboardCharacterRepository"
            ) as MockRepo:
                instance = MockRepo.return_value
                instance.list_by_project = AsyncMock(return_value=[_FAKE_CHAR])
                resp = await client.get(
                    f"{BASE}/projects/{FAKE_PROJECT_ID}/characters"
                )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @pytest.mark.asyncio
    async def test_update_character(self, client: AsyncClient):
        updated = {**_FAKE_CHAR, "name": "Villain"}
        with _svc_patch("update_character", updated):
            resp = await client.put(
                f"{BASE}/characters/{FAKE_CHAR_ID}",
                json={"name": "Villain"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Villain"

    @pytest.mark.asyncio
    async def test_delete_character(self, client: AsyncClient):
        with _svc_patch("delete_character"):
            resp = await client.delete(f"{BASE}/characters/{FAKE_CHAR_ID}")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ===================================================================
# Canvas sync
# ===================================================================


class TestCanvasSync:
    """POST /projects/{id}/sync — atomic incremental canvas sync."""

    @pytest.mark.asyncio
    async def test_sync_canvas_empty(self, client: AsyncClient):
        """Empty sync body is valid (no-op)."""
        with (
            _svc_patch("verify_project_access"),
            _svc_patch("sync_canvas", {"added": 0, "updated": 0, "deleted": 0}),
        ):
            resp = await client.post(
                f"{BASE}/projects/{FAKE_PROJECT_ID}/sync",
                json={},
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_sync_canvas_with_nodes(self, client: AsyncClient):
        sync_result = {"added": 2, "updated": 0, "deleted": 0}
        with (
            _svc_patch("verify_project_access"),
            _svc_patch("sync_canvas", sync_result),
        ):
            resp = await client.post(
                f"{BASE}/projects/{FAKE_PROJECT_ID}/sync",
                json={
                    "added_nodes": [
                        {"node_type": "upload", "position_x": 0, "position_y": 0},
                        {"node_type": "export", "position_x": 100, "position_y": 200},
                    ],
                    "deleted_node_ids": [],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["added"] == 2

    @pytest.mark.asyncio
    async def test_sync_canvas_invalid_node_type(self, client: AsyncClient):
        resp = await client.post(
            f"{BASE}/projects/{FAKE_PROJECT_ID}/sync",
            json={
                "added_nodes": [
                    {"node_type": "INVALID", "position_x": 0, "position_y": 0},
                ],
            },
        )
        assert resp.status_code == 422


# ===================================================================
# Export
# ===================================================================


class TestExport:
    """POST /projects/{id}/export — trigger async export."""

    @pytest.mark.asyncio
    async def test_export_pdf(self, client: AsyncClient):
        with (
            _svc_patch("verify_project_access"),
            patch(
                "app.api.sb_export_router.get_task_manager"
            ) as mock_mgr_fn,
            patch("app.api.sb_export_router.asyncio.to_thread", new_callable=AsyncMock),
        ):
            mock_mgr = AsyncMock()
            mock_mgr.create = AsyncMock(return_value="task-abc")
            mock_mgr_fn.return_value = mock_mgr

            resp = await client.post(
                f"{BASE}/projects/{FAKE_PROJECT_ID}/export",
                json={"format": "pdf"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["task_id"] == "task-abc"

    @pytest.mark.asyncio
    async def test_export_invalid_format(self, client: AsyncClient):
        resp = await client.post(
            f"{BASE}/projects/{FAKE_PROJECT_ID}/export",
            json={"format": "docx"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_export_missing_format(self, client: AsyncClient):
        resp = await client.post(
            f"{BASE}/projects/{FAKE_PROJECT_ID}/export",
            json={},
        )
        assert resp.status_code == 422
