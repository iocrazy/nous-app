"""HTTP tests for the two ``canvas_asset_refs`` reverse lookups (asset lib P4).

  GET /canvases/{canvas_id}/asset-refs   — canvases_router, canvas READ gate
  GET /assets/{asset_id}/canvas-refs     — assets_router, scope gate + Envelope

The gate half is what these pin. The sibling ``GET /canvases/{id}/assets``
shipped with the WRITE guard behind a docstring that said "read" and locked
viewers out of a pure read for months (fixed 2026-08-12) — so "which guard did
this route actually call" is asserted directly, not inferred from a 200.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import AuthContext, get_auth
from app.main import app as main_app
from app.services.assets.assets_service import AssetError

canvases_router = sys.modules["app.api.canvases_router"]

FAKE_USER_ID = str(uuid4())
_REF_ROW = {
    "canvas_id": "5001",
    "canvas_name": "Sang Yao — looks",
    "kind": "smart",
    "project_id": "9000",
    "node_ids": ["asset-1", "asset-2"],
    "loadout_ids": ["727145299382534200"],
}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


# ── GET /canvases/{id}/asset-refs ───────────────────────────────────────────


@pytest.fixture
def _override_auth():
    main_app.dependency_overrides[get_auth] = _fake_auth
    yield
    main_app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client(_override_auth) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=main_app), base_url="http://test"
    ) as ac:
        yield ac


def _repo(monkeypatch, rows):
    repo = SimpleNamespace(list_for_canvas=AsyncMock(return_value=rows))
    monkeypatch.setattr(
        canvases_router, "CanvasAssetRefsRepository", lambda: repo, raising=True
    )
    return repo


@pytest.mark.asyncio
async def test_canvas_asset_refs_returns_the_rows_with_a_count(client, monkeypatch):
    monkeypatch.setattr(
        canvases_router, "_gate_canvas_read", AsyncMock(return_value="9000")
    )
    rows = [
        {
            "asset_id": "727145299382534145",
            "node_id": "asset-1",
            "loadout_id": "727145299382534200",
            "asset_name": "Sang Yao",
            "asset_type": "character",
            "loadout_name": "Night raid",
        }
    ]
    repo = _repo(monkeypatch, rows)

    resp = await client.get("/api/v1/canvases/5001/asset-refs")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": True, "data": rows, "count": 1}
    repo.list_for_canvas.assert_awaited_once_with("5001")


@pytest.mark.asyncio
async def test_canvas_asset_refs_uses_the_read_gate_not_the_write_gate(
    client, monkeypatch
):
    """A viewer-role project member must be able to read this.

    Asserted on the GATE THAT RAN, not on a status code: stubbing both gates to
    succeed and checking for a 200 would pass with either one wired up.
    """
    read_gate = AsyncMock(return_value="9000")
    write_gate = AsyncMock(return_value="9000")
    monkeypatch.setattr(canvases_router, "_gate_canvas_read", read_gate)
    monkeypatch.setattr(canvases_router, "_gate_canvas_write", write_gate)
    _repo(monkeypatch, [])

    resp = await client.get("/api/v1/canvases/5001/asset-refs")

    assert resp.status_code == 200
    read_gate.assert_awaited_once()
    write_gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_canvas_asset_refs_404_when_the_gate_refuses(client, monkeypatch):
    monkeypatch.setattr(
        canvases_router,
        "_gate_canvas_read",
        AsyncMock(
            side_effect=HTTPException(status_code=404, detail="canvas not found")
        ),
    )
    repo = _repo(monkeypatch, [])

    resp = await client.get("/api/v1/canvases/5001/asset-refs")

    assert resp.status_code == 404
    repo.list_for_canvas.assert_not_awaited()  # refused BEFORE the read


@pytest.mark.asyncio
async def test_canvas_asset_refs_403_for_a_non_member(client, monkeypatch):
    monkeypatch.setattr(
        canvases_router,
        "_gate_canvas_read",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="forbidden")),
    )
    _repo(monkeypatch, [])

    resp = await client.get("/api/v1/canvases/5001/asset-refs")

    assert resp.status_code == 403


# ── GET /assets/{id}/canvas-refs ────────────────────────────────────────────


class _AuthStub:
    user_id = FAKE_USER_ID


class _FakeAssetsService:
    """Records the (asset_id, scope_id) pair the route handed down.

    ``scope_id`` is the whole point of this route's safety story — the repo
    filters canvases by it — so the test asserts the value that reached the
    service, not merely that a 200 came back.
    """

    def __init__(self):
        self.calls = []
        self.rows = [_REF_ROW]
        self.error = None

    async def list_canvas_refs(self, asset_id, scope_id):
        self.calls.append((asset_id, scope_id))
        if self.error is not None:
            raise self.error
        return self.rows


@pytest.fixture
def assets_app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    @asynccontextmanager
    async def _no_uow():
        yield None

    application.dependency_overrides[get_auth] = _auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _FakeAssetsService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest_asyncio.fixture
async def assets_client(assets_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=assets_app), base_url="http://t"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_asset_canvas_refs_returns_the_scoped_rows(assets_app, assets_client):
    resp = await assets_client.get(
        "/api/v1/assets/727145299382534145/canvas-refs?scope_id=9000"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": True, "data": [_REF_ROW]}
    # The caller's scope reached the service — this is the filter, not decoration.
    assert assets_app.state.fake.calls == [(727145299382534145, 9000)]


@pytest.mark.asyncio
async def test_asset_canvas_refs_403_for_a_non_member_in_the_router_envelope(
    assets_app, assets_client
):
    """The scope refusal must wear the ``{success:false, error:{code}}`` shape
    every other failure on this router wears — a bare ``{"detail": ...}`` would
    be the one 403 a client cannot read a code off."""
    resp = await assets_client.get(
        "/api/v1/assets/727145299382534145/canvas-refs?scope_id=666"
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["success"] is False and body["error"]["code"] == "not_a_member"
    assert assets_app.state.fake.calls == []


@pytest.mark.asyncio
async def test_asset_canvas_refs_404_when_the_asset_is_not_in_this_scope(
    assets_app, assets_client
):
    """ "No canvases use it" and "that asset is not yours" are different facts.

    The service's ``_require`` answers the second with a typed 404; folding it
    into an empty list would tell a caller their asset is unused when in truth
    they cannot see it at all.
    """
    assets_app.state.fake.error = AssetError(404, "asset_not_found", "Asset not found")

    resp = await assets_client.get(
        "/api/v1/assets/727145299382534145/canvas-refs?scope_id=9000"
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "asset_not_found"


@pytest.mark.asyncio
async def test_asset_canvas_refs_answers_an_empty_list_when_nothing_references_it(
    assets_app, assets_client
):
    """The cross-scope case lands here: canvases outside the caller's scope are
    FILTERED OUT by the repository, so an asset used only elsewhere reads as
    unused rather than 404 — the refusal belongs to the subject, the filter to
    the list (same split as GET /resources/{id}/canvas-refs)."""
    assets_app.state.fake.rows = []

    resp = await assets_client.get(
        "/api/v1/assets/727145299382534145/canvas-refs?scope_id=9000"
    )

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": []}


@pytest.mark.asyncio
async def test_asset_canvas_refs_rejects_a_non_numeric_scope(assets_client):
    """``scope_id`` is ``int()``-ed downstream; a bare str param would turn
    "abc" into an unhandled 500 instead of the 422 the caller earned."""
    resp = await assets_client.get(
        "/api/v1/assets/727145299382534145/canvas-refs?scope_id=abc"
    )
    assert resp.status_code == 422
