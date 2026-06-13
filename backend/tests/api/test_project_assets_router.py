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
            {"id": "111", "filename": "a.png", "role": "reference", "node_id": "s1",
             "file_type": "image", "mime_type": "image/png", "thumbnail_path": None,
             "cover_image_path": None, "created_at": "2026-06-13T00:00:00Z"},
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
