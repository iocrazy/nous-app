# backend/tests/api/test_project_assets_router.py
"""Project-assets endpoints: payload shape + permission gating.

The repo's SQL is faked so these run without a DB; the gating (project
membership / canvas access / resource ownership) is the load-bearing
behavior under test.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
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
