"""Structural + behavior authz tests for script_projects_router (terminal
IDOR close-out, Phase B PR-G2 review).

Structural half mirrors test_projects_authz_wiring.py: every `/{script_id}`
route MUST declare verify_script_access (write routes) or
verify_script_read_access (the GET route — 2026-08-12 fix, gets the
project_members fallback), so a future endpoint added without a guard fails
here instead of shipping an IDOR. Collection routes (create / list) have no
script target and are exempt, matching the projects router.

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
from app.core.scope_guards import verify_script_access, verify_script_read_access
from app.main import app

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())

_KNOWN_GUARDS = {verify_script_access, verify_script_read_access}

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
    assert (
        calls & _KNOWN_GUARDS
    ), f"{key} has no verify_script_access/verify_script_read_access guard"


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

    async def fake_get_by_episode_unowned(self, episode_id):
        return None  # nobody owns this episode yet

    monkeypatch.setattr(ScriptProjectRepository, "update", fake_update)
    monkeypatch.setattr(
        ScriptProjectRepository, "get_by_episode", fake_get_by_episode_unowned
    )

    resp = await client.put("/api/v1/scripts/projects/9001", json={"episode_id": "555"})
    assert resp.status_code == 200
    assert resp.json()["data"]["episode_id"] == "555"


@pytest.mark.asyncio
async def test_reassign_to_episode_owned_by_same_script_ok(client, monkeypatch):
    """Re-saving the episode_id a script already owns (a no-op reassign)
    must NOT be rejected as "already owned" — the owner check excludes the
    requesting script itself."""
    _patch_repos(monkeypatch, script_project_id=700, episode_project_id=700)

    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_update(self, record_id, data):
        return {"id": record_id, "episode_id": data.get("episode_id")}

    async def fake_get_by_episode_self(self, episode_id):
        return {"id": "9001", "episode_id": episode_id}  # owned by THIS script

    monkeypatch.setattr(ScriptProjectRepository, "update", fake_update)
    monkeypatch.setattr(
        ScriptProjectRepository, "get_by_episode", fake_get_by_episode_self
    )

    resp = await client.put("/api/v1/scripts/projects/9001", json={"episode_id": "555"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reassign_to_already_occupied_episode_rejected(client, monkeypatch):
    """The bug this closes: a manual reassign onto an episode ANOTHER live
    script already owns used to succeed unconditionally (a plain UPDATE,
    no existence check) — two scripts on one episode would each derive
    scene numbers 1, 2, 3... independently, producing two "scene 1" in the
    same episode. Must now be rejected (409), not silently allowed."""
    _patch_repos(monkeypatch, script_project_id=700, episode_project_id=700)

    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_update(self, record_id, data):
        raise AssertionError("update() must not be reached — reassign should 409 first")

    async def fake_get_by_episode_occupied(self, episode_id):
        return {"id": "8888", "episode_id": episode_id}  # a DIFFERENT script

    monkeypatch.setattr(ScriptProjectRepository, "update", fake_update)
    monkeypatch.setattr(
        ScriptProjectRepository, "get_by_episode", fake_get_by_episode_occupied
    )

    resp = await client.put("/api/v1/scripts/projects/9001", json={"episode_id": "555"})
    assert resp.status_code == 409
