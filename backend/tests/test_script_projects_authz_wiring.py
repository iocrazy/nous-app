"""Structural + behavior authz tests for script_projects_router (terminal
IDOR close-out, Phase B PR-G2 review).

Structural half mirrors test_projects_authz_wiring.py: every `/{script_id}`
route MUST declare verify_script_access, so a future endpoint added without a
guard fails here instead of shipping an IDOR. Collection routes (create / list)
have no script target and are exempt, matching the projects router.

Behavior half pins the same-project reassign check: PUT with an episode_id that
belongs to a DIFFERENT project is a 404 (no cross-project reassign, no leak).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.script_projects_router import router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_script_access
from app.main import app

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())

# Collection routes: no {script_id} target, nothing script-scoped to guard.
EXEMPT = {
    ("POST", "/scripts/projects"),
    ("GET", "/scripts/projects"),
}


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


@pytest.mark.parametrize(
    "route",
    [r for r in router.routes if hasattr(r, "dependant")],
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_script_project_route_declares_guard(route):
    key = (next(iter(route.methods)), route.path)
    if key in EXEMPT:
        pytest.skip("no script_id target")
    calls = set(_flat_dependency_calls(route.dependant))
    assert verify_script_access in calls, f"{key} has no verify_script_access guard"


# --------------------------------------------------------------------------- #
# Behavior: same-project reassign check
# --------------------------------------------------------------------------- #


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth_and_guard():
    # Bypass the team check — these tests target the reassign check, not authz.
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[verify_script_access] = lambda: None
    yield
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(verify_script_access, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _patch_repos(monkeypatch, script_project_id, episode_project_id):
    from app.repositories.episode_repository import EpisodeRepository
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_script_get(self, script_id):
        return {"id": script_id, "project_id": script_project_id}

    async def fake_episode_get(self, episode_id):
        return {"id": episode_id, "project_id": episode_project_id}

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    monkeypatch.setattr(EpisodeRepository, "get_by_id", fake_episode_get)


@pytest.mark.asyncio
async def test_reassign_to_foreign_project_episode_404(client, monkeypatch):
    # Script in project 700, episode in project 999 → cross-project → 404.
    _patch_repos(monkeypatch, script_project_id=700, episode_project_id=999)

    resp = await client.put("/api/v1/scripts/projects/9001", json={"episode_id": "555"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reassign_within_same_project_ok(client, monkeypatch):
    # Native-int project id on the row vs str on the body — the check must
    # str-coerce both sides (#1006) and pass.
    _patch_repos(monkeypatch, script_project_id=700, episode_project_id=700)

    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_update(self, record_id, data):
        return {"id": record_id, "episode_id": data.get("episode_id")}

    monkeypatch.setattr(ScriptProjectRepository, "update", fake_update)

    resp = await client.put("/api/v1/scripts/projects/9001", json={"episode_id": "555"})
    assert resp.status_code == 200
    assert resp.json()["data"]["episode_id"] == "555"
