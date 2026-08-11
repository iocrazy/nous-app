"""Tests for GET /api/v1/canvases/storyboard — the per-episode system
storyboard canvas get-or-create endpoint (shot-nodes-on-canvas spec
2026-08-11 §2, Task 1).

Router-level tests follow the ASGI-transport + ``app.dependency_overrides``
pattern from ``test_episode_owner.py`` / ``test_canvas_generations_route.py``:
auth is overridden globally, and the two seams the handler calls through
(``get_episode_repository`` and ``verify_project_write_access``, both
imported into ``canvases_router``'s own namespace) are monkeypatched there —
no real DB, no ASGI-level project/episode fixtures needed.

The repository-level test pins the idempotence guarantee itself: a
concurrent duplicate INSERT hitting the partial unique index
(``uq_canvases_storyboard_per_episode``, mig 421) must be caught and
resolved by re-reading the existing row, not surfaced as a 500.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

# ``app.api.__init__`` rebinds ``canvases_router`` to the APIRouter instance,
# so grab the real module from sys.modules (same trick as
# test_canvas_gates.py / test_canvas_generations_route.py).
canvases_router = sys.modules.get("app.api.canvases_router")
if canvases_router is None:  # pragma: no cover - import guard for direct runs
    import app.api.canvases_router as canvases_router  # noqa: F401

    canvases_router = sys.modules["app.api.canvases_router"]

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())
PROJECT_ID = "5001"
EPISODE_ID = "7001"


# --------------------------------------------------------------------------- #
# Shared fixtures
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


def _episode(**overrides: Any) -> Dict[str, Any]:
    row = {
        "id": EPISODE_ID,
        "project_id": PROJECT_ID,
        "title": "Ep 1",
        "sort_order": 1,
    }
    row.update(overrides)
    return row


def _stub_episode_repo(monkeypatch, episode: Optional[Dict[str, Any]]):
    """Wire ``get_episode_repository()`` (imported into canvases_router's own
    namespace) to a fake returning ``episode`` (or None → 404)."""
    captured: Dict[str, Any] = {}

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            captured["get_by_id_called_with"] = episode_id
            return episode

    monkeypatch.setattr(
        canvases_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )
    return captured


def _allow_write_gate(monkeypatch):
    """Bypass ``verify_project_write_access`` — membership itself is not
    under test here (see ``test_canvas_gates.py`` / ``test_scope_guards.py``
    for that); this pins that the handler CALLS the gate with the episode's
    resolved project_id, not that the gate's internals are correct."""
    calls: list = []

    async def _fake(*, project_id, auth):
        calls.append(project_id)

    monkeypatch.setattr(canvases_router, "verify_project_write_access", _fake)
    return calls


def _deny_write_gate(monkeypatch):
    from fastapi import HTTPException

    async def _fake(*, project_id, auth):
        raise HTTPException(status_code=403, detail="You do not have access to this project")

    monkeypatch.setattr(canvases_router, "verify_project_write_access", _fake)


class _FakeCanvasService:
    """In-memory get-or-create store, keyed by (project_id, episode_id) —
    stands in for CanvasService.get_or_create_storyboard so these tests pin
    the ROUTER's behaviour (episode lookup, gating, naming, idempotence
    at the HTTP layer) without touching the repository/DB.
    """

    _next_id = 90000
    store: Dict[tuple, Dict[str, Any]] = {}

    async def get_or_create_storyboard(
        self, *, project_id, episode_id, name, created_by
    ):
        key = (str(project_id), str(episode_id))
        existing = _FakeCanvasService.store.get(key)
        if existing is not None:
            return existing
        _FakeCanvasService._next_id += 1
        row = {
            "id": str(_FakeCanvasService._next_id),
            "project_id": str(project_id),
            "episode_id": str(episode_id),
            "name": name,
            "kind": "storyboard",
            "created_by": created_by,
        }
        _FakeCanvasService.store[key] = row
        return row


@pytest.fixture(autouse=True)
def _stub_canvas_service(monkeypatch):
    _FakeCanvasService.store = {}
    monkeypatch.setattr(canvases_router, "CanvasService", _FakeCanvasService)


# --------------------------------------------------------------------------- #
# Happy path: idempotent get-or-create
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_or_create_creates_once_then_returns_same(client, monkeypatch):
    _stub_episode_repo(monkeypatch, _episode())
    _allow_write_gate(monkeypatch)

    r1 = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")
    assert r1.status_code == 200
    cid = r1.json()["data"]["id"]

    r2 = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == cid  # idempotent, no duplicate row
    assert len(_FakeCanvasService.store) == 1


@pytest.mark.asyncio
async def test_created_canvas_is_storyboard_kind_named_after_episode(
    client, monkeypatch
):
    _stub_episode_repo(monkeypatch, _episode(sort_order=3, title="Ep 3"))
    _allow_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")
    data = resp.json()["data"]

    assert data["kind"] == "storyboard"
    assert "Storyboard" in data["name"]
    assert data["name"] == "EP3 · Storyboard"


@pytest.mark.asyncio
async def test_name_falls_back_to_title_when_sort_order_missing(client, monkeypatch):
    _stub_episode_repo(
        monkeypatch, _episode(sort_order=None, title="Pilot Episode")
    )
    _allow_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")
    data = resp.json()["data"]

    assert data["name"] == "Pilot Episode · Storyboard"


# --------------------------------------------------------------------------- #
# 404 / 403
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_episode_404(client, monkeypatch):
    _stub_episode_repo(monkeypatch, None)

    resp = await client.get(
        "/api/v1/canvases/storyboard?episode_id=999999999999999999"
    )

    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "episode_not_found"


@pytest.mark.asyncio
async def test_non_member_403(client, monkeypatch):
    _stub_episode_repo(monkeypatch, _episode())
    _deny_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Route registration order: 'storyboard' must not be swallowed by the
# dynamic /canvases/{canvas_id} catch-all registered later in the file.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_storyboard_route_not_captured_by_canvas_id_catchall(
    client, monkeypatch
):
    """If route registration order regressed (this route moved below
    ``/canvases/{canvas_id}``), FastAPI would match 'storyboard' as a
    canvas_id and dispatch to ``get_canvas`` instead — which never calls
    the episode repository at all. Asserting the episode lookup fires is a
    direct pin on which handler actually ran."""
    captured = _stub_episode_repo(monkeypatch, _episode())
    _allow_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 200
    assert captured["get_by_id_called_with"] == EPISODE_ID
    # get_canvas's 404 shape ("canvas not found", a bare string) would be
    # the tell if the catch-all had won instead.
    assert resp.json()["data"]["kind"] == "storyboard"


# --------------------------------------------------------------------------- #
# Repository: idempotence is the DB partial unique index, not app logic —
# a concurrent duplicate insert must be caught and resolved by re-reading.
# --------------------------------------------------------------------------- #


class _FakeIntegrityErrorSession:
    """First INSERT raises a 23505 (unique-violation) IntegrityError, as if
    a concurrent request's INSERT won the race against
    ``uq_canvases_storyboard_per_episode``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def execute(self, stmt):
        raise self._exc


def _write_scope_raising(exc: Exception):
    @asynccontextmanager
    async def _scope():
        yield _FakeIntegrityErrorSession(exc)

    return _scope


@pytest.mark.asyncio
async def test_create_storyboard_canvas_reads_back_on_concurrent_unique_violation(
    monkeypatch,
):
    from sqlalchemy.exc import IntegrityError

    import app.repositories.canvas_repository as repo_mod
    from app.repositories.canvas_repository import CanvasRepository

    class _Orig(Exception):
        sqlstate = "23505"

    exc = IntegrityError("insert", {}, _Orig())
    monkeypatch.setattr(repo_mod, "write_scope", _write_scope_raising(exc))

    winner_row = {
        "id": "55555",
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "name": "EP1 · Storyboard",
        "kind": "storyboard",
    }

    async def _fake_get_storyboard_canvas(self, project_id, episode_id):
        assert project_id == PROJECT_ID
        assert episode_id == EPISODE_ID
        return winner_row

    monkeypatch.setattr(
        CanvasRepository, "get_storyboard_canvas", _fake_get_storyboard_canvas
    )

    repo = CanvasRepository()
    result = await repo.create_storyboard_canvas(
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        name="EP1 · Storyboard",
        created_by=None,
    )

    assert result == winner_row  # resolved via re-read, no exception raised


@pytest.mark.asyncio
async def test_create_storyboard_canvas_reraises_non_unique_integrity_errors(
    monkeypatch,
):
    """A non-23505 IntegrityError (e.g. a bad FK) must propagate — only the
    unique-violation code is the idempotence signal."""
    from sqlalchemy.exc import IntegrityError

    import app.repositories.canvas_repository as repo_mod
    from app.repositories.canvas_repository import CanvasRepository

    class _Orig(Exception):
        sqlstate = "23503"

    exc = IntegrityError("insert", {}, _Orig())
    monkeypatch.setattr(repo_mod, "write_scope", _write_scope_raising(exc))

    repo = CanvasRepository()
    with pytest.raises(IntegrityError):
        await repo.create_storyboard_canvas(
            project_id=PROJECT_ID,
            episode_id="0",
            name="EP1 · Storyboard",
            created_by=None,
        )
