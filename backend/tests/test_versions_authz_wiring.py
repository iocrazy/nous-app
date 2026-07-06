"""Authz wiring + behavior tests for the versions router (Phase B P4).

Structural half mirrors test_shots_authz_wiring.py: every route in the versions
router MUST declare a known access guard — a future endpoint added without one
fails here instead of shipping an IDOR. Script-scoped routes take
``verify_script_access`` from the path; the commit-scoped ``DELETE /commits/{id}``
takes ``verify_commit_access`` (resolves commit → script → team).

Behavior half pins the diff shape, the synchronous rollback partial-failure
response, and the commit-message length 422 boundary.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.script_versions_router import router as versions_router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_commit_access, verify_script_access
from app.main import app

pytestmark = pytest.mark.unit

KNOWN_GUARDS = {verify_script_access, verify_commit_access}

FAKE_USER_ID = str(uuid4())


# --------------------------------------------------------------------------- #
# Structural: every route declares a guard
# --------------------------------------------------------------------------- #


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


_ALL_ROUTES = [r for r in list(versions_router.routes) if hasattr(r, "dependant")]


@pytest.mark.parametrize(
    "route",
    _ALL_ROUTES,
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_version_route_declares_guard(route):
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


@pytest.fixture
def _same_team(monkeypatch):
    """Make ``verify_script_access`` pass: script's team == caller's team, and a
    commit fetch resolves to the same script the path names."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "TEAM"}

    async def fake_team(user_id):
        return "TEAM"

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)

    async def fake_member(team_id, user_id):
        return True

    monkeypatch.setattr(guards, "_is_team_member", fake_member)


@pytest.fixture
def _commit_belongs(monkeypatch):
    """A commit fetch always resolves to the script id in the request path."""
    from app.repositories.script_commit_repository import ScriptCommitRepository

    async def fake_get(self, commit_id):
        return {"id": str(commit_id), "script_id": "900"}

    monkeypatch.setattr(ScriptCommitRepository, "get", fake_get)


# --------------------------------------------------------------------------- #
# Behavior: guard resolution (foreign team) mirrors the shots pattern
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_commit_delete_403_foreign_team(client, monkeypatch):
    """A commit resolves to its script's team; a foreign caller gets 403."""
    import app.core.scope_guards as guards
    from app.repositories.script_commit_repository import ScriptCommitRepository
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_commit_get(self, commit_id):
        return {"id": commit_id, "script_id": "9001"}

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "OWNER_TEAM"}

    async def fake_team(user_id):
        return "CALLER_TEAM"

    monkeypatch.setattr(ScriptCommitRepository, "get", fake_commit_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)

    async def fake_member(team_id, user_id):
        return False

    monkeypatch.setattr(guards, "_is_team_member", fake_member)

    resp = await client.delete("/api/v1/commits/123")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_commit_delete_404_missing(client, monkeypatch):
    """A missing commit 404s before any team check."""
    from app.repositories.script_commit_repository import ScriptCommitRepository

    async def fake_get(self, commit_id):
        return None

    monkeypatch.setattr(ScriptCommitRepository, "get", fake_get)
    resp = await client.delete("/api/v1/commits/123")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Behavior: diff shape / rollback partial failure / message length
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_diff_shape(client, monkeypatch, _same_team, _commit_belongs):
    from app.services.script.version_service import VersionService

    async def fake_compute_diff(self, script_id, commit_a, commit_b):
        return {
            "scenes": [
                {"scene_id": "111", "elements": [{"kind": "changed", "id": "el_1"}]}
            ],
            "scenes_added": [{"id": "333"}],
            "scenes_removed": [],
        }

    monkeypatch.setattr(VersionService, "compute_diff", fake_compute_diff)

    resp = await client.get("/api/v1/scripts/900/commits/5000/diff?against=current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["scenes"][0]["scene_id"] == "111"
    assert body["data"]["scenes_added"] == [{"id": "333"}]


@pytest.mark.asyncio
async def test_rollback_partial_failure(
    client, monkeypatch, _same_team, _commit_belongs
):
    from app.services.script.version_service import VersionService

    async def fake_rollback(self, script_id, commit_id, actor):
        return {
            "commit_id": str(commit_id),
            "results": [
                {"scene_id": "111", "status": "rolled_back"},
                {"scene_id": "222", "status": "failed", "error": "boom"},
            ],
            "not_deleted": ["444"],
            "not_resurrected": ["333"],
            "partial_failure": True,
        }

    monkeypatch.setattr(VersionService, "rollback_to", fake_rollback)

    resp = await client.post("/api/v1/scripts/900/commits/5000/rollback")
    assert resp.status_code == 200
    body = resp.json()
    # A partial failure is reported (success False) but not an error status.
    assert body["success"] is False
    statuses = {r["scene_id"]: r["status"] for r in body["data"]["results"]}
    assert statuses == {"111": "rolled_back", "222": "failed"}
    assert body["data"]["not_deleted"] == ["444"]
    assert body["data"]["not_resurrected"] == ["333"]


@pytest.mark.asyncio
async def test_rollback_all_ok_reports_success(
    client, monkeypatch, _same_team, _commit_belongs
):
    from app.services.script.version_service import VersionService

    async def fake_rollback(self, script_id, commit_id, actor):
        return {
            "commit_id": str(commit_id),
            "results": [{"scene_id": "111", "status": "rolled_back"}],
            "not_deleted": [],
            "not_resurrected": [],
            "partial_failure": False,
        }

    monkeypatch.setattr(VersionService, "rollback_to", fake_rollback)
    resp = await client.post("/api/v1/scripts/900/commits/5000/rollback")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_commit_message_too_long_422(client, _same_team):
    resp = await client.post("/api/v1/scripts/900/commits", json={"message": "x" * 201})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_commit_message_empty_422(client, _same_team):
    resp = await client.post("/api/v1/scripts/900/commits", json={"message": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_diff_foreign_commit_404(client, monkeypatch, _same_team):
    """A commit id belonging to a DIFFERENT script 404s even though the caller
    owns the script in the path (no cross-script diff)."""
    from app.repositories.script_commit_repository import ScriptCommitRepository

    async def fake_get(self, commit_id):
        return {"id": str(commit_id), "script_id": "999"}  # different script

    monkeypatch.setattr(ScriptCommitRepository, "get", fake_get)
    resp = await client.get("/api/v1/scripts/900/commits/5000/diff")
    assert resp.status_code == 404
