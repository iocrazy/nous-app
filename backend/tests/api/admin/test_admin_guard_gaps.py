"""Admin routes that were reachable without the admin role.

``/admin/celery/workers`` and ``/admin/celery/queues`` were mounted under the
admin prefix with no dependency at all, so anyone — not even signed in — could
read how many workflows were running and how deep the workforce queue was.
They now take ``AdminAuthDep`` like every other ``/admin`` route.

``test_every_admin_route_requires_the_admin_role`` walks the live app so the
next route added under an ``/admin`` path without the guard fails here, not
in prod.

Each case runs over real HTTP through the real ``get_admin_auth``; only the
role lookup's session is scripted.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext, get_auth
from app.main import app

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"

CELERY_ROUTES = ["/api/v1/admin/celery/workers", "/api/v1/admin/celery/queues"]


class _Row:
    def __init__(self, role: str | None):
        self._role = role

    def first(self) -> Any:
        return None if self._role is None else (self._role,)


@pytest.fixture
def role(monkeypatch):
    """Script the ``user_profiles.role`` lookup in ``get_admin_auth``."""
    box: dict[str, str | None] = {"role": "user"}

    class _Session:
        async def execute(self, *a, **kw):
            return _Row(box["role"])

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    return box


@pytest.fixture
def signed_in():
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture
def dbos_off(monkeypatch):
    from app.services.infra import dbos_orchestrator

    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: False)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CELERY_ROUTES)
async def test_celery_routes_refuse_an_anonymous_caller(client, dbos_off, path):
    resp = await client.get(path)
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CELERY_ROUTES)
async def test_celery_routes_refuse_a_non_admin(
    client, signed_in, role, dbos_off, path
):
    role["role"] = "user"
    resp = await client.get(path)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CELERY_ROUTES)
async def test_celery_routes_serve_an_admin(client, signed_in, role, dbos_off, path):
    role["role"] = "admin"
    resp = await client.get(path)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_unauthenticated_admin_health_is_gone(client):
    """``GET /admin/health`` answered anyone with the ``user_profiles`` row
    count and raw exception text from the database clients. Nothing called
    it; the admin dashboard reads the admin-only ``/system/health``."""
    resp = await client.get("/api/v1/admin/health")
    assert resp.status_code == 404, resp.text


def _depends_on_admin(dependant) -> bool:
    for dep in dependant.dependencies:
        if dep.call is get_admin_auth or _depends_on_admin(dep):
            return True
    return False


def test_every_admin_route_requires_the_admin_role() -> None:
    admin_routes = [
        r for r in app.routes if isinstance(r, APIRoute) and "/admin/" in r.path + "/"
    ]
    assert len(admin_routes) > 100  # the walk found the admin surface at all
    missing = sorted(
        f"{sorted(r.methods)} {r.path}"
        for r in admin_routes
        if not _depends_on_admin(r.dependant)
    )
    assert missing == [], missing
