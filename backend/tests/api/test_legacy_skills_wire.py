"""The legacy ``/api/v1/skills`` CRUD is gone (OpenAPI P5).

Its six routes (and the ``/style-templates`` 301s onto them) had no caller:
the only client code was ``frontend/services/skillService.ts``, which nothing
imported but its own test and an unused re-export shim. Skills are managed
under ``/api/v1/ai-library/skills``. This pins that the old surface does not
come back half-typed, and that removing it left the AI Library one alone.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_legacy_skill_and_style_template_routes_are_not_mounted() -> None:
    paths = _paths()
    legacy = {
        p
        for p in paths
        if p.startswith("/api/v1/skills") or p.startswith("/api/v1/style-templates")
    }
    assert legacy == set()
    assert "/api/v1/ai-library/skills" in paths


def test_legacy_skill_routes_are_not_in_openapi() -> None:
    schema_paths = set(app.openapi()["paths"])
    assert not any(p.startswith("/api/v1/skills") for p in schema_paths)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/v1/skills"),
        ("get", "/api/v1/skills/categories"),
        ("get", "/api/v1/skills/123"),
        ("delete", "/api/v1/skills/123"),
        ("get", "/api/v1/style-templates"),
    ],
)
async def test_legacy_skill_urls_are_404(client, method, path) -> None:
    resp = await getattr(client, method)(path)
    assert resp.status_code == 404
