"""Authz wiring + behavior tests for the shots router (Phase B P3).

Structural half mirrors test_episodes_scenes_authz_wiring.py: every route in the
shots router MUST declare one of the known access guards as a dependency — a
future endpoint added without a guard fails here instead of shipping an IDOR.

Behavior half pins ``verify_shot_access``'s resolution chain: a shot resolves to
its scene → script → team, so a foreign-team caller gets 403 and a missing shot
gets 404 (before the 403), matching the scene/episode guard ordering.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.script_shots_router import router as shots_router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_scene_access, verify_shot_access
from app.main import app

pytestmark = pytest.mark.unit

KNOWN_GUARDS = {verify_scene_access, verify_shot_access}

FAKE_USER_ID = str(uuid4())


# --------------------------------------------------------------------------- #
# Structural: every route declares a guard
# --------------------------------------------------------------------------- #


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


_ALL_ROUTES = [r for r in list(shots_router.routes) if hasattr(r, "dependant")]


@pytest.mark.parametrize(
    "route",
    _ALL_ROUTES,
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_shot_route_declares_guard(route):
    calls = set(_flat_dependency_calls(route.dependant))
    assert (
        calls & KNOWN_GUARDS
    ), f"{sorted(route.methods)} {route.path} declares no access guard"


# --------------------------------------------------------------------------- #
# Behavior fixtures
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Behavior: verify_shot_access resolution chain
# --------------------------------------------------------------------------- #


@pytest.fixture
def _foreign_team(monkeypatch):
    """Wire the guard chain so the caller's team never matches the shot's:
    shot → scene_id → script_id → OWNER_TEAM, while the caller is CALLER_TEAM."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import ScriptProjectRepository
    from app.repositories.script_scene_repository import ScriptSceneRepository
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_shot_get(self, shot_id):
        return {"id": shot_id, "scene_id": "8001"}

    async def fake_scene_get(self, scene_id):
        return {"id": scene_id, "script_id": "9001"}

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "OWNER_TEAM"}

    async def fake_team(user_id):
        return "CALLER_TEAM"

    monkeypatch.setattr(ScriptShotRepository, "get_by_id", fake_shot_get)
    monkeypatch.setattr(ScriptSceneRepository, "get_by_id", fake_scene_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)
    monkeypatch.setattr(guards, "get_team_id_for_user", fake_team)


@pytest.mark.asyncio
async def test_shot_patch_403_foreign_team(client, _foreign_team):
    resp = await client.patch("/api/v1/shots/123", json={"description": "X"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_shot_move_403_foreign_team(client, _foreign_team):
    resp = await client.post("/api/v1/shots/123/move", json={})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_shot_missing_returns_404(client, monkeypatch):
    """A missing shot 404s before any team check (guard row-missing ordering)."""
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_shot_get(self, shot_id):
        return None

    monkeypatch.setattr(ScriptShotRepository, "get_by_id", fake_shot_get)
    resp = await client.delete("/api/v1/shots/123")
    assert resp.status_code == 404
