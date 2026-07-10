"""HTTP tests for the canvas trash lifecycle (Infinite parity G9).

DELETE /canvases/{id} soft-deletes (sets deleted_at); POST
/canvases/{id}/restore brings it back; DELETE /canvases/{id}/purge removes
it permanently but only from the trash; GET /canvases/team/{id}/trash lists
a team's trashed canvases. Listing/tree endpoints exclude trashed rows at
the repository layer.
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


def _svc(monkeypatch, **methods) -> SimpleNamespace:
    svc = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    monkeypatch.setattr(canvases_router, "CanvasService", lambda: svc)
    monkeypatch.setattr(
        canvases_router, "_gate_canvas_write", AsyncMock(return_value="1")
    )
    return svc


class TestSoftDelete:
    @pytest.mark.asyncio
    async def test_delete_soft_deletes(self, client, monkeypatch):
        svc = _svc(monkeypatch, soft_delete=True)
        resp = await client.delete("/api/v1/canvases/5")
        assert resp.status_code == 200
        svc.soft_delete.assert_awaited_once_with("5")

    @pytest.mark.asyncio
    async def test_delete_404_when_missing(self, client, monkeypatch):
        _svc(monkeypatch, soft_delete=False)
        resp = await client.delete("/api/v1/canvases/5")
        assert resp.status_code == 404


class TestRestoreAndPurge:
    @pytest.mark.asyncio
    async def test_restore(self, client, monkeypatch):
        svc = _svc(monkeypatch, restore=True)
        resp = await client.post("/api/v1/canvases/5/restore")
        assert resp.status_code == 200
        svc.restore.assert_awaited_once_with("5")

    @pytest.mark.asyncio
    async def test_restore_404_when_not_in_trash(self, client, monkeypatch):
        _svc(monkeypatch, restore=False)
        resp = await client.post("/api/v1/canvases/5/restore")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_purge(self, client, monkeypatch):
        svc = _svc(monkeypatch, purge=True)
        resp = await client.delete("/api/v1/canvases/5/purge")
        assert resp.status_code == 200
        svc.purge.assert_awaited_once_with("5")

    @pytest.mark.asyncio
    async def test_purge_404_outside_trash(self, client, monkeypatch):
        _svc(monkeypatch, purge=False)
        resp = await client.delete("/api/v1/canvases/5/purge")
        assert resp.status_code == 404


TRASHED = [
    {
        "id": 5,
        "name": "Old Canvas",
        "kind": "smart",
        "updated_at": "2026-01-02T00:00:00Z",
        "deleted_at": "2026-01-03T00:00:00Z",
        "project_id": 11,
        "projects": {"team_id": 900, "name": "Demo Project"},
    }
]


class TestTeamTrashList:
    @pytest.mark.asyncio
    async def test_member_lists_trash(self, client, monkeypatch):
        monkeypatch.setattr(
            canvases_router, "_is_team_member", AsyncMock(return_value=True)
        )
        repo = SimpleNamespace(list_trashed_for_team=AsyncMock(return_value=TRASHED))
        monkeypatch.setattr(canvases_router, "CanvasRepository", lambda: repo)

        resp = await client.get("/api/v1/canvases/team/900/trash")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == [
            {
                "id": "5",
                "name": "Old Canvas",
                "kind": "smart",
                "updated_at": "2026-01-02T00:00:00Z",
                "deleted_at": "2026-01-03T00:00:00Z",
                "project_id": "11",
                "project_name": "Demo Project",
            }
        ]

    @pytest.mark.asyncio
    async def test_non_member_rejected(self, client, monkeypatch):
        monkeypatch.setattr(
            canvases_router, "_is_team_member", AsyncMock(return_value=False)
        )
        resp = await client.get("/api/v1/canvases/team/900/trash")
        assert resp.status_code == 403
