"""Retirement tests for the legacy Storyboard workbench API (Phase B P4 cutover).

The old ReactFlow storyboard workbench — project / character / canvas / AI /
export CRUD under ``/api/v1/storyboard`` (the five ``sb_*`` routers) — was
retired: the storyboarding surface now lives inside the script editor as the
per-scene shot board (``script_shots_router``). The five routers are no longer
registered; a catch-all ``sb_gone_router`` tombstones every ``/storyboard/*``
path with **410 Gone** and a fixed detail.

This file previously exercised each endpoint's CRUD contract. Those behaviours
no longer exist, so it now asserts the tombstone: every representative retired
path answers 410 with the cutover detail. The underlying ``storyboard_*`` tables
are intentionally still present (deferred rename migration), so the ORM repo
tests in ``tests/integration/test_storyboard_repository_orm.py`` are unaffected
and keep running.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.sb_gone_router import LEGACY_STORYBOARD_GONE_DETAIL
from app.core.deps import AuthContext, get_auth
from app.main import app

BASE = "/api/v1/storyboard"
FAKE_USER_ID = "user-test-1234"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    """Bypass real auth for every test in this module."""
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# One representative path per retired sb_* router (method, path). All must 410.
_RETIRED = [
    ("get", f"{BASE}/projects"),  # sb_projects_router (list)
    ("post", f"{BASE}/projects"),  # sb_projects_router (create)
    ("get", f"{BASE}/projects/proj-001"),  # sb_projects_router (get)
    ("post", f"{BASE}/projects/proj-001/sync"),  # sb_canvas_router
    ("post", f"{BASE}/projects/proj-001/characters"),  # sb_characters_router
    ("post", f"{BASE}/generate/image"),  # sb_ai_router
    ("post", f"{BASE}/projects/proj-001/export"),  # sb_export_router
]


class TestLegacyStoryboardRetired:
    """Every retired workbench path is 410-tombstoned with the cutover detail."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", _RETIRED)
    async def test_retired_path_returns_410(
        self, client: AsyncClient, method: str, path: str
    ):
        # GET takes no body; the write verbs send an (ignored) empty JSON body.
        kwargs = {} if method == "get" else {"json": {}}
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 410
        # The app's exception handler renders HTTPException.detail as `error`.
        assert resp.json()["error"] == LEGACY_STORYBOARD_GONE_DETAIL

    @pytest.mark.asyncio
    async def test_convert_to_storyboard_bridge_is_410(self, client: AsyncClient):
        """The script→workbench bridge endpoint is retired too (it wrote the now-
        deprecated storyboard_nodes / script_storyboard_links tables)."""
        resp = await client.post(
            "/api/v1/scripts/convert-to-storyboard",
            json={
                "script_id": "1",
                "chapter_id": "1",
                "storyboard_project_id": "1",
            },
        )
        assert resp.status_code == 410
        assert resp.json()["error"] == LEGACY_STORYBOARD_GONE_DETAIL
