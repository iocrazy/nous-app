"""Wiring tests for the Episode panel's two new backend contracts (Phase B P2,
Task 5).

1. ``PUT /scripts/projects/{id}`` must accept and forward ``episode_id`` — the
   reassign path. ScriptProjectUpdate previously dropped it silently; this pins
   that the schema now carries it through to the repo.
2. ``GET /projects/{id}/episodes`` returns each episode's ``script_count`` so the
   UI can disable deletion of a non-empty episode (episode_id FK is RESTRICT).

Repos are monkeypatched (Phase A wiring style) so the tests pin the HTTP↔schema
contract without a live DB; the SQL itself is exercised on the real-DB pass.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_project_read_access, verify_script_access
from app.main import app
from app.schemas.script import ScriptProjectUpdate

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())


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


def test_script_project_update_schema_carries_episode_id():
    """The reassign field survives model_dump (exclude_none) — the exact payload
    the router forwards to the service."""
    body = ScriptProjectUpdate(episode_id="123")
    assert body.model_dump(exclude_none=True) == {"episode_id": "123"}


@pytest.mark.asyncio
async def test_put_script_project_forwards_episode_id(client, monkeypatch):
    from app.repositories.episode_repository import EpisodeRepository
    from app.repositories.script_repository import ScriptProjectRepository

    captured: dict = {}

    async def fake_update(self, record_id, data):
        captured["record_id"] = record_id
        captured["data"] = data
        return {"id": record_id, "episode_id": data.get("episode_id")}

    # Script + episode both in project 700 so the same-project reassign check
    # passes; then assert the schema forwards episode_id through to the repo.
    async def fake_script_get(self, script_id):
        return {"id": script_id, "project_id": 700}

    async def fake_episode_get(self, episode_id):
        return {"id": episode_id, "project_id": 700}

    monkeypatch.setattr(ScriptProjectRepository, "update", fake_update)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    monkeypatch.setattr(EpisodeRepository, "get_by_id", fake_episode_get)
    # The PUT route now carries verify_script_access — bypass the team check
    # (tested elsewhere) so this test targets the schema passthrough.
    app.dependency_overrides[verify_script_access] = lambda: None
    try:
        resp = await client.put(
            "/api/v1/scripts/projects/9001", json={"episode_id": "555"}
        )
    finally:
        app.dependency_overrides.pop(verify_script_access, None)

    assert resp.status_code == 200
    assert captured["record_id"] == "9001"
    assert captured["data"] == {"episode_id": "555"}
    assert resp.json()["data"]["episode_id"] == "555"


@pytest.mark.asyncio
async def test_list_episodes_returns_script_count(client, monkeypatch):
    from app.repositories.episode_repository import EpisodeRepository

    async def fake_list(self, project_id):
        return [
            {"id": "10", "title": "Ep 1", "sort_order": 0, "script_count": 2},
            {"id": "11", "title": "Ep 2", "sort_order": 1, "script_count": 0},
        ]

    monkeypatch.setattr(EpisodeRepository, "list_by_project", fake_list)
    app.dependency_overrides[verify_project_read_access] = lambda: None
    try:
        resp = await client.get("/api/v1/projects/700/episodes")
    finally:
        app.dependency_overrides.pop(verify_project_read_access, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [e["script_count"] for e in data] == [2, 0]


@pytest.mark.asyncio
async def test_episodes_progress_returns_flat_array(client, monkeypatch):
    """GET /projects/{id}/episodes/progress returns a bare array under
    ``data`` (matching sibling ``GET /episodes``), not a nested
    ``{"items": [...]}`` wrapper (PR-10a review fix 3)."""
    from app.repositories.episode_repository import EpisodeRepository

    async def fake_progress(self, project_id):
        return [
            {
                "episode_id": "10",
                "title": "Ep 1",
                "sort_order": 0,
                "script_count": 1,
                "scene_count": 1,
                "shots_total": 1,
                "shots_done": 1,
                "renders_count": 1,
                "status": "rendered",
            }
        ]

    monkeypatch.setattr(EpisodeRepository, "progress_by_project", fake_progress)
    app.dependency_overrides[verify_project_read_access] = lambda: None
    try:
        resp = await client.get("/api/v1/projects/700/episodes/progress")
    finally:
        app.dependency_overrides.pop(verify_project_read_access, None)

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"], list)
    assert body["data"][0]["episode_id"] == "10"
