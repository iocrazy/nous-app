"""Tests for generated_media_router — list/detail/file/delete endpoints.

Auth override pattern mirrors test_canvas_classic_node_route.py:
  app.dependency_overrides[get_auth] = _fake_auth
using get_auth from app.core.deps (the real dependency that AuthDep resolves).

NOTE: app/api/__init__.py rebinds ``app.api.generated_media_router`` to the
APIRouter instance, so ``import app.api.generated_media_router as r`` yields
the router object. Use sys.modules to get the real module (same pattern as
test_canvas_classic_node_route.py for canvases_router).
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import app.main first so __init__.py runs and populates sys.modules.
from app.core.deps import AuthContext, get_auth
from app.main import app

# Must use sys.modules — __init__.py rebinds the attribute name to the APIRouter.
r = sys.modules["app.api.generated_media_router"]

FAKE_USER_ID = "00000000-0000-0000-0000-000000000042"


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


@pytest.mark.asyncio
async def test_list_uses_caller_personal_scope(monkeypatch, client):
    """list_generations must resolve the caller's personal team scope_id and
    forward it to the repository — asserted from inside the patched method."""

    async def _fake_scope(uid: str) -> str:
        return "42"

    async def _fake_list(scope_id: int, **k):
        assert scope_id == 42, f"expected scope_id=42, got {scope_id!r}"
        return {"items": [{"id": 1}], "next_cursor": None}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(
        r.GeneratedMediaRepository,
        "list_for_scope",
        lambda self, sid, **k: _fake_list(sid, **k),
    )

    resp = await client.get("/api/v1/generated-media")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["items"] == [{"id": 1}]
    assert body["data"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_passes_kind_filter(monkeypatch, client):
    """kind query param is forwarded to the repository."""
    captured: dict = {}

    async def _fake_scope(uid: str) -> str:
        return "7"

    async def _fake_list(scope_id: int, *, kind=None, cursor=None, limit=30):
        captured["kind"] = kind
        captured["scope_id"] = scope_id
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(
        r.GeneratedMediaRepository,
        "list_for_scope",
        lambda self, sid, **k: _fake_list(sid, **k),
    )

    resp = await client.get("/api/v1/generated-media?kind=image")
    assert resp.status_code == 200, resp.text
    assert captured["kind"] == "image"
    assert captured["scope_id"] == 7


@pytest.mark.asyncio
async def test_get_detail_404_when_not_in_scope(monkeypatch, client):
    """GET /{id} returns 404 when repo.get returns None (out of scope)."""

    async def _fake_scope(uid: str) -> str:
        return "99"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return None

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)

    resp = await client.get("/api/v1/generated-media/1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_detail_returns_data_envelope(monkeypatch, client):
    """GET /{id} wraps the row in {data: row} when found."""

    async def _fake_scope(uid: str) -> str:
        return "5"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return {"id": gen_id, "scope_id": scope_id, "media_kind": "image"}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)

    resp = await client.get("/api/v1/generated-media/123")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["id"] == 123
    assert body["data"]["media_kind"] == "image"


@pytest.mark.asyncio
async def test_delete_returns_deleted_bool(monkeypatch, client):
    """DELETE /{id} returns {data: {deleted: bool}}."""

    async def _fake_scope(uid: str) -> str:
        return "3"

    async def _fake_delete(self, gen_id: int, scope_id: int) -> bool:
        return True

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "delete", _fake_delete)

    resp = await client.delete("/api/v1/generated-media/7")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["deleted"] is True
