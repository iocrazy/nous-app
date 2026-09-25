"""``DELETE /agent-memory/{memory_id}``: ownership and wire parity (P9).

Real HTTP through the real repository; only the session is scripted. It
answers the DELETE the way Postgres would for a table holding one memory
owned by ``OWNER``: the row goes when the statement names it and — if the
SQL carries an owner condition — the bound owner is ``OWNER``. Take the
owner condition out of the SQL and a stranger's delete succeeds.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from tests.api.wire_parity import assert_wire_unchanged

repo_mod = sys.modules["app.repositories.agent_memory_repository"]

pytestmark = pytest.mark.unit

OWNER = "00000000-0000-0000-0000-000000000042"
TEAMMATE = "00000000-0000-0000-0000-000000000099"
MEMORY_ID = 7300000000000000123

_CALLER = {"user_id": OWNER}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=_CALLER["user_id"], auth_type="jwt")


class _Result:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    _CALLER["user_id"] = OWNER

    class _Session:
        async def execute(self, stmt, params=None, *a, **kw):
            params = params or {}
            sql = str(stmt)
            owner_ok = (
                "owner_user_id = :user_id" not in sql
                or str(params.get("user_id")) == OWNER
            )
            hit = params.get("id") == MEMORY_ID and owner_ok
            return _Result(1 if hit else 0)

    @asynccontextmanager
    async def _write_scope():
        yield _Session()

    monkeypatch.setattr(repo_mod, "write_scope", _write_scope)
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_owner_delete_wire_unchanged(client):
    resp = await client.delete(f"/api/v1/agent-memory/{MEMORY_ID}")
    assert_wire_unchanged(resp, {"deleted": True})


@pytest.mark.asyncio
async def test_teammate_cannot_delete_owners_memory(client):
    _CALLER["user_id"] = TEAMMATE
    resp = await client.delete(f"/api/v1/agent-memory/{MEMORY_ID}")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_unknown_memory_is_typed_404(client):
    resp = await client.delete("/api/v1/agent-memory/1")
    _assert_typed_404(resp)
