"""Assets endpoints: payload shape + error mapping + scope gating (no DB)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError

USER = "11111111-1111-1111-1111-111111111111"


class _AuthStub:
    user_id = USER


class _FakeService:
    def __init__(self):
        self.calls = []

    async def list_assets(self, scope_id, **f):
        self.calls.append(("list", scope_id, f))
        return [{"id": "1", "name": "Sang Yao", "asset_type": "character"}]

    async def create_asset(self, scope_id, payload, user_id):
        if payload.name == "dup":
            raise AssetError(409, "asset_exists", "exists", {"existing_asset_id": "7"})
        return {"id": "2", "name": payload.name, "asset_type": payload.asset_type}

    async def get_asset(self, asset_id, scope_id):
        if asset_id == 404:
            raise AssetError(404, "asset_not_found", "nope")
        return {
            "id": str(asset_id),
            "files": [],
            "links": [],
            "linked_by": [],
            "loadouts": [],
        }

    async def attach_file(self, asset_id, scope_id, req, user_id):
        return {
            "asset_id": str(asset_id),
            "resource_id": req.resource_id,
            "slot": req.slot,
        }

    async def add_link(self, asset_id, scope_id, req):
        raise AssetError(422, "link_not_allowed", "no", {})


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest.mark.asyncio
async def test_list_passes_filters_and_wraps(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/assets?scope_id=9000&type=character&project_id=55&q=sang"
        )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["data"][0]["name"] == "Sang Yao"
    _, scope, f = app.state.fake.calls[0]
    assert (
        scope == 9000
        and f["asset_type"] == "character"
        and f["project_id"] == 55
        and f["q"] == "sang"
    )


@pytest.mark.asyncio
async def test_non_member_403(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=666")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_201_and_409_shape(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "Blade"}
        )
        assert r.status_code == 201 and r.json()["data"]["id"] == "2"
        r = await c.post(
            "/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "dup"}
        )
    assert r.status_code == 409
    err = r.json()
    assert err["success"] is False
    assert (
        err["error"]["code"] == "asset_exists"
        and err["error"]["existing_asset_id"] == "7"
    )


@pytest.mark.asyncio
async def test_get_404_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/404?scope_id=9000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "asset_not_found"


@pytest.mark.asyncio
async def test_attach_single_and_batch(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"resource_id": "727145299382534146", "slot": "sheet"},
        )
        assert r.status_code == 201 and r.json()["data"]["slot"] == "sheet"
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={
                "items": [{"resource_id": "1"}, {"resource_id": "2", "slot": "stills"}]
            },
        )
    assert r.status_code == 201 and len(r.json()["data"]) == 2


@pytest.mark.asyncio
async def test_link_422_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/links?scope_id=9000",
            json={"to_asset_id": "6", "relation": "wears"},
        )
    assert r.status_code == 422 and r.json()["error"]["code"] == "link_not_allowed"
