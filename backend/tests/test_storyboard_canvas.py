"""Tests for GET /api/v1/canvases/storyboard — the per-episode system
storyboard canvas get-or-create endpoint (shot-nodes-on-canvas spec
2026-08-11 §2, Task 1).

Router-level tests follow the ASGI-transport + ``app.dependency_overrides``
pattern from ``test_episode_owner.py`` / ``test_canvas_generations_route.py``:
auth is overridden globally, and the two seams the handler calls through
(``get_episode_repository`` and ``verify_project_write_access``, both
imported into ``canvases_router``'s own namespace) are monkeypatched there —
no real DB, no ASGI-level project/episode fixtures needed.

The repository-level tests pin the idempotence guarantee itself: a
concurrent duplicate INSERT hitting the partial unique index
(``uq_canvases_storyboard_per_episode``, mig 421/422) must be caught and
resolved — either by re-reading the winning LIVE row, or (repair round 1,
mig 422) by RESTORING a SOFT-DELETED row the index still reserves the slot
for — never surfaced as a 500.
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
from app.core.scope_guards import ProjectAccess
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


def _stub_episode_repo(
    monkeypatch,
    episode: Optional[Dict[str, Any]],
    siblings: Optional[list] = None,
):
    """Wire ``get_episode_repository()`` (imported into canvases_router's own
    namespace) to a fake returning ``episode`` (or None → 404) from
    ``get_by_id``, and ``siblings`` (defaults to just ``[episode]``, or
    ``[]`` when ``episode`` is None) from ``list_by_project`` — the
    sort_order-ascending list the handler now uses to derive the 1-based
    display rank (repair round 1)."""
    captured: Dict[str, Any] = {}
    sib_list = siblings if siblings is not None else ([episode] if episode else [])

    class _FakeEpisodeRepo:
        async def get_by_id(self, episode_id):
            captured["get_by_id_called_with"] = episode_id
            return episode

        async def list_by_project(self, project_id):
            captured["list_by_project_called_with"] = project_id
            return sib_list

    monkeypatch.setattr(
        canvases_router, "get_episode_repository", lambda: _FakeEpisodeRepo()
    )
    return captured


def _allow_write_gate(monkeypatch):
    """Bypass BOTH ``verify_project_write_access`` and
    ``resolve_project_read_access`` — membership itself is not under test
    here (see ``test_canvas_gates.py`` / ``test_scope_guards.py`` for that);
    this pins that the handler CALLS the appropriate gate with the
    episode's resolved project_id, not that the gate's internals are
    correct. Both are stubbed because the get-or-create flow now takes the
    READ gate once a canvas already exists (2026-08-12 gate split) — most
    callers here don't care which branch runs, just that whichever gate
    fires passes."""
    calls: list = []

    async def _fake_write(*, project_id, auth):
        calls.append(project_id)

    async def _fake_read(*, project_id, auth):
        calls.append(project_id)
        return ProjectAccess(can_read=True, can_write=True)

    monkeypatch.setattr(canvases_router, "verify_project_write_access", _fake_write)
    monkeypatch.setattr(canvases_router, "resolve_project_read_access", _fake_read)
    return calls


def _deny_write_gate(monkeypatch):
    from fastapi import HTTPException

    async def _fake(*, project_id, auth):
        raise HTTPException(
            status_code=403, detail="You do not have access to this project"
        )

    monkeypatch.setattr(canvases_router, "verify_project_write_access", _fake)


def _deny_read_gate(monkeypatch):
    from fastapi import HTTPException

    async def _fake(*, project_id, auth):
        raise HTTPException(
            status_code=403, detail="You do not have access to this project"
        )

    monkeypatch.setattr(canvases_router, "resolve_project_read_access", _fake)


def _allow_read_gate(monkeypatch):
    async def _fake(*, project_id, auth):
        return ProjectAccess(can_read=True, can_write=True)

    monkeypatch.setattr(canvases_router, "resolve_project_read_access", _fake)


class _FakeCanvasService:
    """In-memory get-or-create store, keyed by (project_id, episode_id) —
    stands in for CanvasService.get_or_create_storyboard so these tests pin
    the ROUTER's behaviour (episode lookup, gating, naming, idempotence
    at the HTTP layer) without touching the repository/DB.
    """

    _next_id = 90000
    store: Dict[tuple, Dict[str, Any]] = {}

    async def peek_storyboard(self, project_id, episode_id):
        return _FakeCanvasService.store.get((str(project_id), str(episode_id)))

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
    ep1 = _episode(id="7000", sort_order=1, title="Ep 1")
    ep2 = _episode(id="7000b", sort_order=2, title="Ep 2")
    target = _episode(sort_order=3, title="Ep 3")  # id=EPISODE_ID, rank 3rd
    _stub_episode_repo(monkeypatch, target, siblings=[ep1, ep2, target])
    _allow_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")
    data = resp.json()["data"]

    assert data["kind"] == "storyboard"
    assert "Storyboard" in data["name"]
    assert data["name"] == "EP3 · Storyboard"


@pytest.mark.asyncio
async def test_name_rank_not_raw_sort_order_after_middle_delete(client, monkeypatch):
    """Repair round 1 (评审 Important #2): ``sort_order`` is an
    ever-increasing counter — deleting an episode never renumbers the
    survivors (confirmed against episode_repository.py). Three episodes
    were created (sort_order 1/2/3); episode 2 was then deleted. The
    remaining 'episode 3' still carries sort_order=3, but its RANK among
    the two survivors is 2nd — the canvas name must say EP2, matching the
    workspace sidebar's own ``epNumber = epIdx + 1`` convention, not EP3."""
    ep1 = _episode(id="9001", sort_order=1, title="Ep 1")
    ep3_survivor = _episode(sort_order=3, title="Ep 3")  # id=EPISODE_ID
    _stub_episode_repo(monkeypatch, ep3_survivor, siblings=[ep1, ep3_survivor])
    _allow_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")
    data = resp.json()["data"]

    assert data["name"] == "EP2 · Storyboard"


@pytest.mark.asyncio
async def test_name_falls_back_to_title_when_episode_not_in_its_own_project_list(
    client, monkeypatch
):
    """Defensive fallback: if the episode can't be located in its own
    project's episode list (shouldn't happen in practice), rank is None and
    the name falls back to the episode's title instead of e.g. 'EP{None}'."""
    target = _episode(title="Pilot Episode")
    _stub_episode_repo(monkeypatch, target, siblings=[])  # target absent
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

    resp = await client.get("/api/v1/canvases/storyboard?episode_id=999999999999999999")

    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "episode_not_found"


@pytest.mark.asyncio
async def test_non_member_403(client, monkeypatch):
    _stub_episode_repo(monkeypatch, _episode())
    _deny_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Gate split (2026-08-12 fix): existing canvas → read gate; missing → write
# gate. Before this fix EVERY call (including one that only reads an
# already-existing canvas) went through verify_project_write_access, so a
# viewer-role project member got a 403 just opening a storyboard someone
# else had already created.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_existing_storyboard_uses_read_gate_not_write_gate(client, monkeypatch):
    """A viewer (write gate denies, read gate allows) can fetch an ALREADY
    EXISTING storyboard canvas — proves the read branch, not the write
    branch, gates this case."""
    _stub_episode_repo(monkeypatch, _episode())
    _deny_write_gate(monkeypatch)
    _allow_read_gate(monkeypatch)

    existing = {
        "id": "42",
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "name": "EP1 · Storyboard",
        "kind": "storyboard",
    }
    _FakeCanvasService.store[(PROJECT_ID, EPISODE_ID)] = existing

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "42"


@pytest.mark.asyncio
async def test_missing_storyboard_still_denied_by_write_gate(client, monkeypatch):
    """A viewer (read gate allows, write gate denies) requesting a
    NOT-YET-CREATED storyboard still 403s — the read fallback must never
    let a read-only caller conjure a new canvas via this GET."""
    _stub_episode_repo(monkeypatch, _episode())
    _allow_read_gate(monkeypatch)
    _deny_write_gate(monkeypatch)

    resp = await client.get(f"/api/v1/canvases/storyboard?episode_id={EPISODE_ID}")

    assert resp.status_code == 403
    assert not _FakeCanvasService.store  # nothing was created


# --------------------------------------------------------------------------- #
# Route registration order: 'storyboard' must not be swallowed by the
# dynamic /canvases/{canvas_id} catch-all registered later in the file.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_storyboard_route_not_captured_by_canvas_id_catchall(client, monkeypatch):
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


# --------------------------------------------------------------------------- #
# Repository: mig 422 self-heal — a 23505 conflict against a SOFT-DELETED
# storyboard row (generic DELETE /canvases/{id} doesn't know about kind)
# must RESTORE that row, not return None (which used to surface as a
# permanent 500 for the episode — the index still reserved the slot for the
# trashed row before 422 added ``AND deleted_at IS NULL``).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_storyboard_canvas_restores_soft_deleted_row_on_conflict(
    monkeypatch,
):
    from sqlalchemy.exc import IntegrityError

    import app.repositories.canvas_repository as repo_mod
    from app.repositories.canvas_repository import CanvasRepository

    class _Orig(Exception):
        sqlstate = "23505"

    exc = IntegrityError("insert", {}, _Orig())
    monkeypatch.setattr(repo_mod, "write_scope", _write_scope_raising(exc))

    trashed_row = {
        "id": "66666",
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "name": "EP1 · Storyboard",
        "kind": "storyboard",
        "deleted_at": "2026-08-11T00:00:00+00:00",
    }
    restored_row = {**trashed_row, "deleted_at": None}

    calls: Dict[str, Any] = {"live_lookups": 0, "trashed_lookups": 0, "restore": []}

    async def _fake_get_storyboard_canvas(
        self, project_id, episode_id, *, include_trashed=False
    ):
        assert project_id == PROJECT_ID
        assert episode_id == EPISODE_ID
        if include_trashed:
            calls["trashed_lookups"] += 1
            return trashed_row
        calls["live_lookups"] += 1
        # 1st live-only call (pre-restore): nothing live yet.
        # 2nd live-only call (post-restore re-read): the row is live now.
        return restored_row if calls["restore"] else None

    async def _fake_restore(self, canvas_id):
        calls["restore"].append(canvas_id)
        return True

    monkeypatch.setattr(
        CanvasRepository, "get_storyboard_canvas", _fake_get_storyboard_canvas
    )
    monkeypatch.setattr(CanvasRepository, "restore", _fake_restore)

    repo = CanvasRepository()
    result = await repo.create_storyboard_canvas(
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        name="EP1 · Storyboard",
        created_by=None,
    )

    assert calls["restore"] == ["66666"]  # restored, not re-inserted
    assert calls["live_lookups"] == 2  # pre-restore miss + post-restore re-read
    assert calls["trashed_lookups"] == 1
    assert result == restored_row
    assert result["id"] == trashed_row["id"]  # SAME canvas id — self-heal


@pytest.mark.asyncio
async def test_create_storyboard_canvas_conflict_with_no_resolvable_row_returns_none(
    monkeypatch,
):
    """23505 but neither a live nor a trashed row is visible (e.g. a
    same-transaction race we can't see yet) — must return None, the
    pre-existing failure signal, rather than raise or fabricate a row."""
    from sqlalchemy.exc import IntegrityError

    import app.repositories.canvas_repository as repo_mod
    from app.repositories.canvas_repository import CanvasRepository

    class _Orig(Exception):
        sqlstate = "23505"

    exc = IntegrityError("insert", {}, _Orig())
    monkeypatch.setattr(repo_mod, "write_scope", _write_scope_raising(exc))

    async def _fake_get_storyboard_canvas(
        self, project_id, episode_id, *, include_trashed=False
    ):
        return None

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

    assert result is None
