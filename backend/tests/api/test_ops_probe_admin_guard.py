"""``/health/deep`` and ``/dbos/routing`` are platform-admin only.

Both were anonymous. ``/health/deep`` answered anyone with the raw exception
text of the database / Redis clients (connection errors name hosts and
ports), model-health state and in-process registry names, and ran a database
read per request; ``/dbos/routing`` forced a database refresh per request and
returned the internal task-type routing table. Neither has a caller in the
repo (container healthchecks and the deploy smoke use ``/api/v1/readyz``).

Pairs: anonymous → 401, signed in but not admin → 403 (no probe runs),
admin → 200.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.db.session as db_session_mod
from app.core.deps import AuthContext, get_auth
from app.main import app

health_mod = sys.modules["app.api.health_router"]
dbos_orch = sys.modules["app.services.infra.dbos_orchestrator"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
ROUTES = ["/api/v1/health/deep", "/api/v1/dbos/routing"]


class _Probe:
    role: str | None = None
    ran: list[str] = []


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    _Probe.role = None
    _Probe.ran = []

    class _Row:
        def first(self):
            return None if _Probe.role is None else (_Probe.role,)

    class _Session:
        async def execute(self, stmt):
            return _Row()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _supabase() -> str:
        _Probe.ran.append("supabase")
        return "reachable, 1 row sample"

    async def _redis() -> str:
        _Probe.ran.append("redis")
        return "reachable"

    async def _refresh() -> None:
        _Probe.ran.append("routing")

    monkeypatch.setattr(db_session_mod, "read_scope", _scope)
    monkeypatch.setattr(health_mod, "_probe_supabase", _supabase)
    monkeypatch.setattr(health_mod, "_probe_redis", _redis)
    monkeypatch.setattr(dbos_orch, "_refresh_routing_cache", _refresh)
    yield
    app.dependency_overrides.pop(get_auth, None)


def _sign_in() -> None:
    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ROUTES)
async def test_anonymous_is_rejected(client, path) -> None:
    resp = await client.get(path)
    assert resp.status_code == 401, resp.text
    assert _Probe.ran == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", None])
@pytest.mark.parametrize("path", ROUTES)
async def test_non_admin_is_rejected(client, path, role: Any) -> None:
    _sign_in()
    _Probe.role = role
    resp = await client.get(path)
    assert resp.status_code == 403, resp.text
    assert _Probe.ran == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ROUTES)
async def test_admin_is_allowed(client, path) -> None:
    _sign_in()
    _Probe.role = "admin"
    resp = await client.get(path)
    assert resp.status_code == 200, resp.text
    assert _Probe.ran
