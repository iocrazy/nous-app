"""Tests for generated_media_router — promote endpoint.

Auth override pattern mirrors test_generated_media_router.py:
  app.dependency_overrides[get_auth] = _fake_auth
using get_auth from app.core.deps (the real dependency that AuthDep resolves).

NOTE: app/api/__init__.py rebinds ``app.api.generated_media_router`` to the
APIRouter instance, so ``import app.api.generated_media_router as r`` yields
the router object. Use sys.modules to get the real module (same pattern as
test_generated_media_router.py for generated_media_router).
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
async def test_promote_route_returns_resource_id(monkeypatch, client):
    """POST /{gen_id}/promote calls the service and returns
    {data: {promoted_resource_id: str}} — WITHOUT naming a destination scope.

    This test used to assert ``target_scope_id == 42``, i.e. that the route
    pinned the caller's personal team as the destination. That was the defect,
    not the contract: a team board's generation had its Tier-2 copy pulled into
    whichever member opened an editor on it. The destination is now the
    generation's own scope, resolved by the service, so the route must pass
    no scope at all.
    """

    seen: dict = {}

    class _FakeSvc:
        async def promote(self, *, gen_id, user_id, **extra):
            seen.update(extra)
            return {"id": 555}

    monkeypatch.setattr(r, "PromoteGeneratedMediaService", lambda: _FakeSvc())

    resp = await client.post("/api/v1/generated-media/7/promote")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["promoted_resource_id"] == "555"
    assert "target_scope_id" not in seen, (
        "the route named a destination again — the service must default to "
        "the generation's own scope"
    )


@pytest.mark.asyncio
async def test_promote_route_404_when_not_in_scope(monkeypatch, client):
    """POST /{gen_id}/promote returns 404 when the service raises ValueError
    (no such generation, or the caller may not read the scope it lives in)."""

    class _FakeSvcNotFound:
        async def promote(self, *, gen_id, user_id, **_extra):
            raise ValueError("generation not found")

    monkeypatch.setattr(r, "PromoteGeneratedMediaService", lambda: _FakeSvcNotFound())

    resp = await client.post("/api/v1/generated-media/999/promote")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_route_establishes_ambient_scope(monkeypatch, client):
    """Prod 2026-08-21: promote 500ed with UnscopedQueryError — the route
    called the service with no ambient Scope, so the Resources INSERT hit
    the scoped-ORM guard. The route must wrap the call in request_scope."""
    from app.db.scope import current_scope

    seen: dict = {}

    class _FakeSvc:
        async def promote(self, *, gen_id, user_id, **_extra):
            seen["scope"] = current_scope()
            return {"id": 777}

    monkeypatch.setattr(r, "PromoteGeneratedMediaService", lambda: _FakeSvc())
    resp = await client.post("/api/v1/generated-media/1/promote")
    assert resp.status_code == 200, resp.text
    assert seen.get("scope") is not None, "service ran without an ambient scope"
    assert str(seen["scope"].user_id) == FAKE_USER_ID
