"""``can_edit`` on the canvas LOAD responses (2026-08-13).

PR #1820 taught the frontend to latch read-only *after* an autosave came
back 403. That is a diagnosis by collision: every viewer load still cost
one PUT that was guaranteed to fail, and until it returned the surface
offered edit gestures whose results were silently dropped. The fix is to
ship the verdict WITH the read:

    GET /api/v1/canvases/{canvas_id}        → data.can_edit
    GET /api/v1/canvases/storyboard?…       → data.can_edit

Both read it from ``scope_guards.can_write_project`` — the non-raising
twin of the guard the PUT itself runs (see ``test_scope_guards.py`` for
the matrix + the no-drift pin). Here we only pin the WIRING: that each
load endpoint asks the predicate for the canvas's own project and puts
the answer in the payload.

ASGI-transport + ``app.dependency_overrides`` style, matching
``test_storyboard_canvas.py`` / ``test_canvas_generations_route.py``:
auth overridden globally, the seams monkeypatched in the router's own
namespace. No DB.

Fixtures use the real wire shape of these endpoints — the canvases
router stringifies its snowflake ids (``str(out["id"])``), so ``id`` /
``project_id`` are JSON strings, deliberately unlike the scenes/shots
routers which return them as JSON numbers.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

canvases_router = sys.modules.get("app.api.canvases_router")
if canvases_router is None:  # pragma: no cover - import guard for direct runs
    import app.api.canvases_router as canvases_router  # noqa: F401

    canvases_router = sys.modules["app.api.canvases_router"]

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())
PROJECT_ID = "5001"
EPISODE_ID = "7001"
CANVAS_ID = "337610660408263"


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


def _canvas_row() -> Dict[str, Any]:
    return {
        "id": CANVAS_ID,
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "name": "EP1 · Storyboard",
        "kind": "storyboard",
        "viewport_json": {"x": 0, "y": 0, "zoom": 1},
        "nodes_json": [],
        "connections_json": [],
        "node_ops_json": [],
        "connection_ops_json": [],
        "base_updated_at": "2026-08-13T09:00:00+00:00",
        "created_at": "2026-08-01T09:00:00+00:00",
        "updated_at": "2026-08-13T09:00:00+00:00",
        "created_by": None,
    }


def _stub_can_write(monkeypatch, verdict: bool) -> list:
    """Wire ``can_write_project`` (imported into canvases_router's own
    namespace) to a recorder returning ``verdict``. Its internals are
    covered by test_scope_guards.py; here we pin that the router asks it
    about the CANVAS'S OWN project."""
    asked: list = []

    async def _fake(project_id, user_id):
        asked.append((str(project_id), str(user_id)))
        return verdict

    monkeypatch.setattr(canvases_router, "can_write_project", _fake)
    return asked


# --------------------------------------------------------------------------- #
# GET /canvases/{canvas_id}
# --------------------------------------------------------------------------- #


@pytest.fixture
def _stub_canvas_get(monkeypatch):
    class _FakeCanvasService:
        async def get_project_id(self, canvas_id):
            return PROJECT_ID

        async def get(self, canvas_id):
            return _canvas_row()

    monkeypatch.setattr(canvases_router, "CanvasService", _FakeCanvasService)


@pytest.fixture
def _allow_read(monkeypatch):
    async def _fake(*, project_id, auth):
        return None

    monkeypatch.setattr(canvases_router, "verify_project_read_access", _fake)


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [True, False])
async def test_get_canvas_reports_can_edit(
    client, monkeypatch, _stub_canvas_get, _allow_read, verdict
):
    """An owner/manager/editor gets can_edit=true; a viewer (read gate
    passes, write predicate says no) gets can_edit=false — WITHOUT having
    to send a PUT to find out."""
    asked = _stub_can_write(monkeypatch, verdict)

    resp = await client.get(f"/api/v1/canvases/{CANVAS_ID}")

    assert resp.status_code == 200
    assert resp.json()["data"]["can_edit"] is verdict
    assert asked == [(PROJECT_ID, FAKE_USER_ID)]


@pytest.mark.asyncio
async def test_get_canvas_non_member_still_403(client, monkeypatch, _stub_canvas_get):
    """can_edit is an addition to the read payload, not a replacement for
    the read gate — a non-member never reaches a payload at all."""

    async def _deny(*, project_id, auth):
        raise HTTPException(status_code=403, detail="nope")

    monkeypatch.setattr(canvases_router, "verify_project_read_access", _deny)
    _stub_can_write(monkeypatch, True)

    resp = await client.get(f"/api/v1/canvases/{CANVAS_ID}")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_canvas_keeps_snowflake_ids_as_strings(
    client, monkeypatch, _stub_canvas_get, _allow_read
):
    """Regression fence around ``_to_response``'s new kwarg: the ids must
    stay JSON strings (JS loses precision above 2^53)."""
    _stub_can_write(monkeypatch, True)

    data = (await client.get(f"/api/v1/canvases/{CANVAS_ID}")).json()["data"]

    assert data["id"] == CANVAS_ID and isinstance(data["id"], str)
    assert data["project_id"] == PROJECT_ID and isinstance(data["project_id"], str)
    assert isinstance(data["episode_id"], str)


# --------------------------------------------------------------------------- #
# GET /canvases/storyboard
# --------------------------------------------------------------------------- #


class _FakeStoryboardService:
    """Get-or-create stub. ``existing`` decides which branch the router
    takes — the read branch (canvas already there) or the write branch."""

    existing: Optional[Dict[str, Any]] = None
    created: list = []

    async def peek_storyboard(self, project_id, episode_id):
        return _FakeStoryboardService.existing

    async def get_or_create_storyboard(
        self, *, project_id, episode_id, name, created_by
    ):
        row = {**_canvas_row(), "name": name}
        _FakeStoryboardService.created.append(row)
        return row


@pytest.fixture
def _stub_storyboard(monkeypatch):
    _FakeStoryboardService.existing = None
    _FakeStoryboardService.created = []
    monkeypatch.setattr(canvases_router, "CanvasService", _FakeStoryboardService)

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            return {
                "id": EPISODE_ID,
                "project_id": PROJECT_ID,
                "title": "Ep 1",
                "sort_order": 1,
            }

        async def list_by_project(self, project_id):
            return [{"id": EPISODE_ID}]

    monkeypatch.setattr(
        canvases_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )
    return _FakeStoryboardService


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [True, False])
async def test_existing_storyboard_reports_can_edit(
    client, monkeypatch, _stub_storyboard, _allow_read, verdict
):
    """The read branch (canvas already exists — the one a viewer can
    reach) carries the verdict."""
    _stub_storyboard.existing = _canvas_row()
    asked = _stub_can_write(monkeypatch, verdict)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 200
    assert resp.json()["data"]["can_edit"] is verdict
    assert asked == [(PROJECT_ID, FAKE_USER_ID)]


@pytest.mark.asyncio
async def test_created_storyboard_reports_can_edit_true_without_asking_again(
    client, monkeypatch, _stub_storyboard
):
    """The create branch runs only after the WRITE guard passed, so the
    answer is already known — can_edit=true, no second round trip."""

    async def _allow(*, project_id, auth):
        return None

    monkeypatch.setattr(canvases_router, "verify_project_write_access", _allow)
    asked = _stub_can_write(monkeypatch, False)  # must not be consulted

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 200
    assert resp.json()["data"]["can_edit"] is True
    assert asked == []
    assert len(_stub_storyboard.created) == 1
