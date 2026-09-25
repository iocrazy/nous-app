"""``/points/admin/*``: the platform-admin gate.

These two routes checked the role with a private ``_check_admin_role`` and
``AuthDep`` instead of ``AdminAuthDep``, so no test that walks the app for
"every ``/admin/`` route depends on ``get_admin_auth``" could see them. The
adjust route now takes ``AdminAuthDep``; ``GET /points/admin/overview`` had no
caller anywhere (frontend, admin, browser, scripts, nous-core, backend) and
duplicated ``/admin/credits/stats``, so it is gone along with
``PointsRepository.get_admin_overview``.

Each case runs over real HTTP through the real ``get_admin_auth``; only the
role lookup's session and the points service are scripted.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext, get_auth
from app.main import app

pytestmark = pytest.mark.unit

points_router = sys.modules["app.api.points_router"]

USER = "00000000-0000-0000-0000-000000000042"
TEAM_ID = "7300000000000000009"


class _Row:
    def __init__(self, role: str | None):
        self._role = role

    def first(self) -> Any:
        return None if self._role is None else (self._role,)

    def scalars(self) -> Any:
        # The team-existence lookup in ``require_team``: TEAM_ID exists.
        class _S:
            def all(self):
                return [int(TEAM_ID)]

        return _S()


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


@pytest.fixture(autouse=True)
def signed_in():
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture
def adjust_calls(monkeypatch) -> list[dict[str, Any]]:
    """Every call that would move money, recorded instead of executed."""
    calls: list[dict[str, Any]] = []

    async def _add(self, team_id, amount, type, description, user_id=None, **kw):
        calls.append({"team_id": team_id, "amount": amount, "user_id": user_id})
        return {"success": True, "new_balance": 600}

    monkeypatch.setattr(points_router.PointsService, "add_points", _add)
    return calls


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


_BODY = {"team_id": TEAM_ID, "amount": 100, "description": "Top-up"}


def test_adjust_route_depends_on_get_admin_auth() -> None:
    route = next(
        r for r in app.routes if getattr(r, "path", "") == "/api/v1/points/admin/adjust"
    )
    calls = {d.call for d in route.dependant.dependencies}
    assert get_admin_auth in calls


@pytest.mark.asyncio
async def test_adjust_refused_for_a_non_admin(role, adjust_calls, client) -> None:
    role["role"] = "user"
    resp = await client.post("/api/v1/points/admin/adjust", json=_BODY)
    assert resp.status_code == 403, resp.text
    assert adjust_calls == []


@pytest.mark.asyncio
async def test_adjust_refused_without_a_profile(role, adjust_calls, client) -> None:
    role["role"] = None
    resp = await client.post("/api/v1/points/admin/adjust", json=_BODY)
    assert resp.status_code == 403, resp.text
    assert adjust_calls == []


@pytest.mark.asyncio
async def test_adjust_allowed_for_an_admin(role, adjust_calls, client) -> None:
    role["role"] = "admin"
    resp = await client.post("/api/v1/points/admin/adjust", json=_BODY)
    assert resp.status_code == 200, resp.text
    assert adjust_calls == [{"team_id": TEAM_ID, "amount": 100, "user_id": USER}]


@pytest.mark.asyncio
async def test_overview_route_is_gone(role, client) -> None:
    role["role"] = "admin"
    resp = await client.get("/api/v1/points/admin/overview")
    assert resp.status_code in (404, 405), resp.text
    assert not any(
        getattr(r, "path", "") == "/api/v1/points/admin/overview" for r in app.routes
    )
