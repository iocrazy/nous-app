"""Authz wiring + behavior tests for episodes / scenes routers (Phase B P2).

Structural half mirrors test_projects_authz_wiring.py: every route in the two
new routers MUST declare one of the known access guards as a dependency — a
future endpoint added without a guard fails here instead of shipping an IDOR.

Behavior half pins the load-bearing HTTP contracts that the structural test
can't see: the script_canvas chapter IDOR fix (403 for a foreign team), and
the elements/ops optimistic-concurrency envelope (428 without If-Match, the
409 VersionConflict shape, the 422 OpError shape).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.episodes_router import router as episodes_router
from app.api.script_scenes_router import router as scenes_router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_episode_write_access,
    verify_project_read_access,
    verify_project_write_access,
    verify_scene_access,
    verify_script_access,
)
from app.main import app

pytestmark = pytest.mark.unit

KNOWN_GUARDS = {
    verify_project_read_access,
    verify_project_write_access,
    verify_episode_write_access,
    verify_script_access,
    verify_scene_access,
}

FAKE_USER_ID = str(uuid4())


# --------------------------------------------------------------------------- #
# Structural: every route declares a guard
# --------------------------------------------------------------------------- #


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


_ALL_ROUTES = [
    r
    for r in (list(episodes_router.routes) + list(scenes_router.routes))
    if hasattr(r, "dependant")
]


@pytest.mark.parametrize(
    "route",
    _ALL_ROUTES,
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_scene_route_declares_guard(route):
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
# Behavior: script_canvas chapter IDOR fix (403 for a foreign team)
# --------------------------------------------------------------------------- #


@pytest.fixture
def _foreign_team(monkeypatch):
    """Wire the guard chain so the caller's team never matches the script's."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import (
        ScriptChapterRepository,
        ScriptProjectRepository,
    )

    async def fake_chapter_get(self, chapter_id):
        return {"id": chapter_id, "script_id": "9001"}

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "OWNER_TEAM"}

    async def fake_team(user_id):
        return "CALLER_TEAM"

    monkeypatch.setattr(ScriptChapterRepository, "get_by_id", fake_chapter_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)
    monkeypatch.setattr(guards, "get_team_id_for_user", fake_team)


@pytest.mark.asyncio
async def test_canvas_update_chapter_403_foreign_team(client, _foreign_team):
    resp = await client.put(
        "/api/v1/scripts/projects/chapters/123", json={"title": "X"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_canvas_delete_chapter_403_foreign_team(client, _foreign_team):
    resp = await client.delete("/api/v1/scripts/projects/chapters/123")
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Behavior: elements/ops optimistic-concurrency envelope
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _bypass_scene_guard():
    """Scene-ops behavior tests target the ops contract, not the guard — pass
    the scene access check unconditionally."""
    app.dependency_overrides[verify_scene_access] = lambda: None
    yield
    app.dependency_overrides.pop(verify_scene_access, None)


@pytest.mark.asyncio
async def test_ops_missing_if_match_returns_428(client):
    resp = await client.post(
        "/api/v1/scenes/555/elements/ops",
        json={"ops": [{"op": "insert", "element_id": "el_1"}]},
    )
    assert resp.status_code == 428


@pytest.mark.asyncio
async def test_ops_version_conflict_returns_409_shape(client, monkeypatch):
    from app.repositories.script_scene_repository import (
        ScriptSceneRepository,
        VersionConflict,
    )

    async def fake_apply(self, scene_id, ops, expected_version, actor):
        raise VersionConflict(7, [{"id": "el_1", "type": "action"}])

    monkeypatch.setattr(ScriptSceneRepository, "apply_element_ops", fake_apply)

    resp = await client.post(
        "/api/v1/scenes/555/elements/ops",
        headers={"If-Match": "3"},
        json={"ops": [{"op": "insert", "element_id": "el_1"}]},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "version_conflict"
    assert body["current_version"] == 7
    assert body["elements"] == [{"id": "el_1", "type": "action"}]


@pytest.mark.asyncio
async def test_ops_op_error_returns_422_shape(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository
    from app.services.script.scene_ops import OpError

    async def fake_apply(self, scene_id, ops, expected_version, actor):
        raise OpError("missing_anchor", "before_id el_x not found")

    monkeypatch.setattr(ScriptSceneRepository, "apply_element_ops", fake_apply)

    resp = await client.post(
        "/api/v1/scenes/555/elements/ops",
        headers={"If-Match": "3"},
        json={"ops": [{"op": "insert", "element_id": "el_1", "before_id": "el_x"}]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "missing_anchor"
    assert "el_x" in body["detail"]


@pytest.mark.asyncio
async def test_ops_success_returns_200_envelope(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_apply(self, scene_id, ops, expected_version, actor):
        assert actor == FAKE_USER_ID
        return {"content_version": 4, "elements": [{"id": "el_1"}]}

    monkeypatch.setattr(ScriptSceneRepository, "apply_element_ops", fake_apply)

    resp = await client.post(
        "/api/v1/scenes/555/elements/ops",
        headers={"If-Match": "3"},
        json={"ops": [{"op": "insert", "element_id": "el_1"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["content_version"] == 4
    assert body["data"]["elements"] == [{"id": "el_1"}]
