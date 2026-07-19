"""HTTP-layer tests for the M3 ``target_duration_sec`` field on the script
project update endpoint (PUT /api/v1/scripts/projects/{id}).

Mirrors test_script_beats.py: override auth, wire the ``verify_script_access``
chain (project → team → membership) so the guard passes, and mock the service
so the captured payload is asserted at the boundary. The point of the 422 cases
is that an out-of-INTEGER-range target never reaches asyncpg (22003 → opaque
500) — it is rejected at the Pydantic boundary just like the beat arrangement
fields.
"""

from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

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


@pytest.fixture
def _member_of_team(monkeypatch):
    """Guard chain passes: script → TEAM_1, caller is a member."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "TEAM_1"}

    async def fake_member(team_id, user_id):
        return True

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)
    monkeypatch.setattr(guards, "_is_team_member", fake_member)


@pytest.mark.asyncio
async def test_update_forwards_target_duration_sec(
    client, _member_of_team, monkeypatch
):
    """A valid target flows to the repo update as a native int."""
    captured: Dict[str, Any] = {}

    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_update(self, record_id, data):
        captured.update(data)
        return {"id": record_id, "target_duration_sec": data.get("target_duration_sec")}

    monkeypatch.setattr(ScriptProjectRepository, "update", fake_update)

    resp = await client.put(
        "/api/v1/scripts/projects/9001", json={"target_duration_sec": 2700}
    )
    assert resp.status_code == 200
    assert captured == {"target_duration_sec": 2700}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        2_147_483_648,  # PG INTEGER max + 1 → 22003 if forwarded
        -1,  # negative runtime is nonsense
    ],
)
async def test_update_rejects_out_of_range_target(client, _member_of_team, value):
    """Out-of-range seconds 422 at the schema boundary, never reaching the repo."""
    resp = await client.put(
        "/api/v1/scripts/projects/9001", json={"target_duration_sec": value}
    )
    assert resp.status_code == 422
