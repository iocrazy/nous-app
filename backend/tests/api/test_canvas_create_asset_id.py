"""POST /projects/{id}/canvases with ``asset_id`` — the scope check.

``canvases.asset_id`` (mig 446) FK-references ``assets(id)``, and the FK alone
does not care WHOSE asset it is: without a check, a member of team A could hang
their project's canvas off team B's asset. The row would be legal, the write
would answer 201, and the leak would only surface as a name rendered in a
project nobody on that team belongs to.

An asset's scope is a ``teams.id``; the project's is its ``team_id``, or — for
a personal project (``team_id`` NULL) — the OWNER's personal team, the same
resolution ``GET /projects/{id}/assets`` performs.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth

# ``app.api`` re-exports the canvases APIRouter under this very name, so
# ``from app.api import canvases_router`` hands back the router object, not the
# module (the same trap ``app/api/__init__.py`` documents for assets_router).
cr = importlib.import_module("app.api.canvases_router")

USER = "11111111-1111-1111-1111-111111111111"
OWNER = "22222222-2222-2222-2222-222222222222"
ASSET_ID = 727145299382534145


class _AuthStub:
    user_id = USER


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(cr.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth

    async def _write_ok(*, project_id, auth):
        return None

    monkeypatch.setattr(cr, "verify_project_write_access", _write_ok)
    return application


class _FakeCanvasService:
    """Records what the router asked the service to persist."""

    created: dict = {}

    async def create_in_project(self, project_id, data, created_by):
        _FakeCanvasService.created = {
            "project_id": project_id,
            "asset_id": data.asset_id,
            "created_by": created_by,
        }
        return {
            "id": 10001,
            "project_id": int(project_id),
            "name": data.name,
            "kind": data.kind,
            "asset_id": int(data.asset_id) if data.asset_id else None,
            "created_by": created_by,
            "created_at": "2026-08-29T00:00:00+00:00",
            "updated_at": "2026-08-29T00:00:00+00:00",
        }


@pytest.fixture
def fake_service(monkeypatch):
    _FakeCanvasService.created = {}
    monkeypatch.setattr(cr, "CanvasService", _FakeCanvasService)
    return _FakeCanvasService


def _stub_project(monkeypatch, *, team_id, owner_id=OWNER, exists=True):
    async def _project_team_id(self, project_id):
        return exists, team_id, owner_id

    monkeypatch.setattr(
        cr.AssetRelationsRepository, "project_team_id", _project_team_id
    )


def _stub_asset(monkeypatch, *, in_scopes):
    seen = []

    async def _get(self, asset_id, scope_id):
        seen.append((int(asset_id), int(scope_id)))
        return {"id": int(asset_id)} if int(scope_id) in in_scopes else None

    monkeypatch.setattr(cr.AssetsRepository, "get", _get)
    return seen


@pytest.mark.asyncio
async def test_asset_in_the_projects_team_is_persisted(app, monkeypatch, fake_service):
    _stub_project(monkeypatch, team_id=9000)
    seen = _stub_asset(monkeypatch, in_scopes={9000})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/projects/55/canvases",
            json={"name": "Sang Yao board", "asset_id": str(ASSET_ID)},
        )
    assert r.status_code == 200, r.text
    assert seen == [(ASSET_ID, 9000)]
    assert fake_service.created["asset_id"] == str(ASSET_ID)
    # BIGINT out of a JS number's reach — it must leave as a string.
    assert r.json()["data"]["asset_id"] == str(ASSET_ID)


@pytest.mark.asyncio
async def test_asset_from_another_team_is_404(app, monkeypatch, fake_service):
    _stub_project(monkeypatch, team_id=9000)
    _stub_asset(monkeypatch, in_scopes={7777})  # the asset lives elsewhere
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/projects/55/canvases", json={"asset_id": str(ASSET_ID)}
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "asset_not_found"
    assert fake_service.created == {}, "nothing may be written on a refusal"


@pytest.mark.asyncio
async def test_personal_project_resolves_the_owners_team(
    app, monkeypatch, fake_service
):
    """team_id NULL → the owner's personal team, not the caller's."""
    _stub_project(monkeypatch, team_id=None, owner_id=OWNER)
    seen = _stub_asset(monkeypatch, in_scopes={777})
    resolved = {}

    async def _resolve(user_id):
        resolved["user_id"] = user_id
        return "777"

    monkeypatch.setattr(cr, "_resolve_personal_team_id", _resolve)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/projects/55/canvases", json={"asset_id": str(ASSET_ID)}
        )
    assert r.status_code == 200, r.text
    assert resolved["user_id"] == OWNER
    assert seen == [(ASSET_ID, 777)]


@pytest.mark.asyncio
async def test_personal_project_whose_owner_has_no_team_is_404(
    app, monkeypatch, fake_service
):
    _stub_project(monkeypatch, team_id=None, owner_id=OWNER)
    _stub_asset(monkeypatch, in_scopes={777})

    async def _no_team(user_id):
        raise ValueError("No personal team")

    monkeypatch.setattr(cr, "_resolve_personal_team_id", _no_team)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/projects/55/canvases", json={"asset_id": str(ASSET_ID)}
        )
    assert r.status_code == 404 and r.json()["detail"] == "asset_not_found"


@pytest.mark.asyncio
async def test_no_asset_id_asks_no_scope_question(app, monkeypatch, fake_service):
    """The field is optional; the check must not run (nor 404) without it."""

    async def _explode(self, project_id):  # pragma: no cover - must not be called
        raise AssertionError("project_team_id must not be consulted without asset_id")

    monkeypatch.setattr(cr.AssetRelationsRepository, "project_team_id", _explode)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/projects/55/canvases", json={"name": "Plain"})
    assert r.status_code == 200, r.text
    assert fake_service.created["asset_id"] is None
    assert r.json()["data"]["asset_id"] is None
