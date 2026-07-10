"""HTTP tests for GET /api/v1/canvases/team/{team_id} (canvas nav N+1 fix).

One call returns every project in the team with its canvases embedded as
SUMMARY columns only (no nodes_json payload) — replaces the landing page's
fetchProjects + per-project listCanvases fan-out. Team-membership gated.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

canvases_router = sys.modules["app.api.canvases_router"]

FAKE_USER_ID = str(uuid4())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


TREE = [
    {
        "id": 11,
        "name": "Demo Project",
        "canvases": [
            {
                "id": 5,
                "name": "Hero",
                "kind": "smart",
                "updated_at": "2026-01-02T00:00:00Z",
            },
        ],
    },
    {"id": 12, "name": "Empty Project", "canvases": []},
]


class TestTeamCanvasList:
    @pytest.mark.asyncio
    async def test_member_gets_the_project_tree(self, client, monkeypatch):
        monkeypatch.setattr(
            canvases_router, "_is_team_member", AsyncMock(return_value=True)
        )
        repo = SimpleNamespace(list_team_tree=AsyncMock(return_value=TREE))
        monkeypatch.setattr(canvases_router, "CanvasRepository", lambda: repo)

        resp = await client.get("/api/v1/canvases/team/900")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [p["project_name"] for p in data] == ["Demo Project", "Empty Project"]
        # Empty projects survive (the landing page offers New Canvas there).
        assert data[1]["canvases"] == []
        repo.list_team_tree.assert_awaited_once_with("900")

    @pytest.mark.asyncio
    async def test_non_member_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(
            canvases_router, "_is_team_member", AsyncMock(return_value=False)
        )
        resp = await client.get("/api/v1/canvases/team/900")
        assert resp.status_code == 403
