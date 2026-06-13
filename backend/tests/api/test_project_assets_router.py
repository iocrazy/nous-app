# backend/tests/api/test_project_assets_router.py
"""Project-assets endpoints: payload shape + permission gating.

The repo's SQL is faked so these run without a DB; the gating (project
membership / canvas access / resource ownership) is the load-bearing
behavior under test.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import project_assets_router as par
from app.core.deps import get_auth


class _AuthStub:
    user_id = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(par.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth
    return application


@pytest.mark.asyncio
async def test_canvas_assets_returns_items(app, monkeypatch):
    async def fake_gate(canvas_id, auth):
        return "9000"

    async def fake_list(self, canvas_id):
        return [
            {
                "id": "111",
                "filename": "a.png",
                "role": "reference",
                "node_id": "s1",
                "file_type": "image",
                "mime_type": "image/png",
                "thumbnail_path": None,
                "cover_image_path": None,
                "created_at": "2026-06-13T00:00:00Z",
            },
        ]

    monkeypatch.setattr(par, "_gate_canvas_read", fake_gate)
    monkeypatch.setattr(par.CanvasRefsRepository, "list_assets_for_canvas", fake_list)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/canvases/5001/assets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"][0]["id"] == "111"
    assert body["data"][0]["role"] == "reference"


@pytest.mark.asyncio
async def test_canvas_assets_403_when_not_member(app, monkeypatch):
    async def deny(canvas_id, auth):
        raise HTTPException(status_code=403, detail="not a member")

    monkeypatch.setattr(par, "_gate_canvas_read", deny)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/canvases/5001/assets")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_resource_canvas_refs_returns_canvases(app, monkeypatch):
    async def allow(resource_id, user_id, team_id):
        return True

    async def fake_list(self, resource_id):
        return [
            {
                "canvas_id": "5001",
                "canvas_name": "Board A",
                "kind": "smart",
                "project_id": "9000",
                "role": "reference",
            },
        ]

    monkeypatch.setattr(par, "check_media_access", allow)
    monkeypatch.setattr(
        par.CanvasRefsRepository, "list_canvases_for_resource", fake_list
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/resources/111/canvas-refs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["canvas_id"] == "5001"
    assert body["count"] == 1


@pytest.mark.asyncio
async def test_resource_canvas_refs_404_when_no_access(app, monkeypatch):
    async def deny(resource_id, user_id, team_id):
        return False

    monkeypatch.setattr(par, "check_media_access", deny)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/resources/111/canvas-refs")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_project_assets_tree_groups_by_project(app, monkeypatch):
    async def fake_projects(self, user_id, team_id=None):
        return [
            {"id": "9000", "name": "Proj One", "team_id": None},
            {"id": "9001", "name": "Proj Two", "team_id": None},
        ]

    async def fake_tree(self, project_ids):
        assert set(project_ids) == {"9000", "9001"}
        return [
            {
                "project_id": "9000",
                "canvas_id": "5001",
                "canvas_name": "A",
                "kind": "smart",
                "asset_count": 3,
            },
            {
                "project_id": "9000",
                "canvas_id": "5002",
                "canvas_name": "B",
                "kind": "classic",
                "asset_count": 0,
            },
        ]

    monkeypatch.setattr(par.ProjectsRepository, "get_user_projects", fake_projects)
    monkeypatch.setattr(par.CanvasRefsRepository, "tree_for_projects", fake_tree)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/resources/project-assets/tree")
    assert resp.status_code == 200
    data = resp.json()["data"]
    proj_one = next(p for p in data if p["project_id"] == "9000")
    assert proj_one["name"] == "Proj One"
    assert len(proj_one["canvases"]) == 2
    assert {c["canvas_id"] for c in proj_one["canvases"]} == {"5001", "5002"}
    proj_two = next(p for p in data if p["project_id"] == "9001")
    assert proj_two["canvases"] == []
