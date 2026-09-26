"""Canvas routes: wire parity after they gained response models (P3).

Each JSON route is driven over real HTTP with a repository-shaped row that
carries every column, and the body must equal what FastAPI sent for the
bare dict (``tests/api/wire_parity.py`` explains why). The row models are
pinned to their source: ``CanvasRow`` to the ORM columns, the summary models
to the columns the repository actually SELECTs (captured from the statement,
not retyped by hand).
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import ProjectAccess
from app.main import app
from app.models import Canvases
from app.schemas.canvas_responses import (
    CanvasAssetRef,
    CanvasGenerationCapability,
    CanvasRow,
    CanvasSummary,
    ProjectTrashedCanvas,
    TeamCanvasSummary,
    TeamTrashedCanvas,
)
from tests.api.canvas_wire_rows import repo_canvas_row
from tests.api.wire_parity import SAMPLE_TS, assert_wire_unchanged, column_names

r = sys.modules["app.api.canvases_router"]
repo_mod = sys.modules["app.repositories.canvas_repository"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
PROJECT_ID = "7300000000000000001"
TEAM_ID = "7300000000000000009"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _gates(monkeypatch):
    """Every gate allows; the gates themselves are covered elsewhere."""
    app.dependency_overrides[get_auth] = _fake_auth

    async def _allow(*, project_id, auth):
        return None

    async def _read(*, project_id, auth):
        return ProjectAccess(can_read=True, can_write=True)

    async def _member(team_id, user_id):
        return True

    monkeypatch.setattr(r, "verify_project_read_access", _allow)
    monkeypatch.setattr(r, "verify_project_write_access", _allow)
    monkeypatch.setattr(r, "resolve_project_read_access", _read)
    monkeypatch.setattr(r, "_is_team_member", _member)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _row_with_nulls() -> Dict[str, Any]:
    """Every nullable canvas column null."""
    return repo_canvas_row(
        episode_id=None,
        asset_id=None,
        created_by=None,
        deleted_at=None,
    )


class _Service:
    """Hands back fixed repository rows; records nothing."""

    row: Dict[str, Any] = {}
    summaries: List[Dict[str, Any]] = []
    trashed: List[Dict[str, Any]] = []
    ok = True

    async def get_project_id(self, canvas_id):
        return PROJECT_ID

    async def get(self, canvas_id):
        return _Service.row

    async def update_with_lock(self, canvas_id, payload):
        return _Service.row

    async def create_in_project(self, project_id, payload, created_by):
        return _Service.row

    async def peek_storyboard(self, project_id, episode_id):
        return _Service.row

    async def list_for_project(self, project_id):
        return _Service.summaries

    async def list_trashed_for_project(self, project_id):
        return _Service.trashed

    async def soft_delete(self, canvas_id):
        return _Service.ok

    async def restore(self, canvas_id):
        return _Service.ok

    async def purge(self, canvas_id):
        return _Service.ok


@pytest.fixture
def service(monkeypatch):
    _Service.row = repo_canvas_row()
    _Service.summaries = []
    _Service.trashed = []
    _Service.ok = True
    monkeypatch.setattr(r, "CanvasService", _Service)
    return _Service


# --------------------------------------------------------------------------- #
# Model ↔ source pins
# --------------------------------------------------------------------------- #


class _CapturingSession:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    async def execute(self, stmt):
        self.sink.append(stmt)

        class _Result:
            def mappings(self):
                class _M:
                    def all(self):
                        return []

                    def first(self):
                        return None

                return _M()

        return _Result()


def test_canvas_row_declares_every_orm_column() -> None:
    assert set(CanvasRow.model_fields) - {"can_edit"} == column_names(Canvases)


@pytest.mark.asyncio
async def test_summary_models_match_the_repository_selects(monkeypatch) -> None:
    sink: list = []

    @asynccontextmanager
    async def _scope():
        yield _CapturingSession(sink)

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    repo = repo_mod.CanvasRepository()
    await repo.list_for_project("1")
    await repo.list_trashed_for_project("1")
    await repo.list_trashed_for_team("1")
    listed, trashed, team_trashed = (
        {c.key for c in stmt.selected_columns} for stmt in sink
    )
    assert set(CanvasSummary.model_fields) == listed
    assert set(ProjectTrashedCanvas.model_fields) == trashed
    # The team trash repo row carries the owning project's name and team id
    # under private labels; the router keeps the name as project_name.
    assert set(TeamTrashedCanvas.model_fields) == (
        team_trashed - {"_project_team_id", "_project_name"}
    ) | {"project_name"}


# --------------------------------------------------------------------------- #
# Full documents
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_get_wire_unchanged(client, service, nulls) -> None:
    if nulls:
        service.row = _row_with_nulls()
    resp = await client.get("/api/v1/canvases/7300000000000000123")
    expected = r._to_response(service.row, can_edit=True)
    assert_wire_unchanged(resp, {"success": True, "data": expected})
    data = resp.json()["data"]
    assert data["can_edit"] is True
    assert isinstance(data["id"], str)


@pytest.mark.asyncio
async def test_put_wire_unchanged_and_carries_no_can_edit(client, service) -> None:
    resp = await client.put(
        "/api/v1/canvases/7300000000000000123",
        json={"base_updated_at": SAMPLE_TS.isoformat(), "name": "Renamed"},
    )
    assert_wire_unchanged(resp, {"success": True, "data": r._to_response(service.row)})
    assert "can_edit" not in resp.json()["data"]


@pytest.mark.asyncio
async def test_create_wire_unchanged(client, service) -> None:
    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/canvases", json={"name": "Board"}
    )
    assert_wire_unchanged(resp, {"success": True, "data": r._to_response(service.row)})
    assert "can_edit" not in resp.json()["data"]


@pytest.mark.asyncio
async def test_existing_storyboard_wire_unchanged(client, service, monkeypatch) -> None:
    service.row = repo_canvas_row(kind="storyboard")

    class _Episodes:
        async def get_by_id(self, episode_id):
            return {"id": int(episode_id), "project_id": int(PROJECT_ID)}

    monkeypatch.setattr(r, "get_episode_repository", lambda: _Episodes())
    resp = await client.get("/api/v1/canvases/storyboard?episode_id=77")
    expected = r._to_response(service.row, can_edit=True)
    assert_wire_unchanged(resp, {"success": True, "data": expected})


# --------------------------------------------------------------------------- #
# Lists and acks
# --------------------------------------------------------------------------- #


def _summary_row(**overrides: Any) -> Dict[str, Any]:
    full = repo_canvas_row(**overrides)
    keep = set(CanvasSummary.model_fields) - {"node_count"}
    return {**{k: full[k] for k in keep}, "node_count": 3}


@pytest.mark.asyncio
async def test_project_list_wire_unchanged(client, service) -> None:
    service.summaries = [_summary_row(), _summary_row(kind="lite")]
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/canvases")
    expected = [r._to_response(s) for s in service.summaries]
    assert_wire_unchanged(resp, {"success": True, "data": expected})
    assert resp.json()["data"][0]["node_count"] == 3


@pytest.mark.asyncio
async def test_project_trash_wire_unchanged(client, service) -> None:
    full = repo_canvas_row()
    service.trashed = [
        {k: full[k] for k in ProjectTrashedCanvas.model_fields},
        {**{k: full[k] for k in ProjectTrashedCanvas.model_fields}, "kind": None},
    ]
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/canvases/trash")
    expected = [
        {
            "id": str(t["id"]),
            "name": t["name"] or "",
            "kind": t["kind"] or "smart",
            "updated_at": t["updated_at"],
            "deleted_at": t["deleted_at"],
            "project_id": str(t["project_id"]),
        }
        for t in service.trashed
    ]
    assert_wire_unchanged(resp, {"success": True, "data": expected})


@pytest.mark.asyncio
async def test_team_tree_wire_unchanged(client, monkeypatch) -> None:
    full = repo_canvas_row()
    canvas = {k: full[k] for k in TeamCanvasSummary.model_fields}
    tree = [
        {"id": 7300000000000000001, "name": "Alpha", "canvases": [canvas]},
        {"id": 7300000000000000002, "name": "Empty", "canvases": []},
    ]

    async def _tree(self, team_id):
        return tree

    monkeypatch.setattr(r.CanvasRepository, "list_team_tree", _tree)
    resp = await client.get(f"/api/v1/canvases/team/{TEAM_ID}")
    expected = [
        {
            "project_id": str(p["id"]),
            "project_name": p["name"],
            "canvases": [
                {
                    "id": str(c["id"]),
                    "name": c["name"],
                    "kind": c["kind"],
                    "updated_at": c["updated_at"],
                }
                for c in p["canvases"]
            ],
        }
        for p in tree
    ]
    assert_wire_unchanged(resp, {"success": True, "data": expected})


@pytest.mark.asyncio
async def test_team_trash_wire_unchanged(client, monkeypatch) -> None:
    full = repo_canvas_row()
    row = {
        **{k: full[k] for k in ProjectTrashedCanvas.model_fields},
        "projects": {"team_id": int(TEAM_ID), "name": "Alpha"},
    }

    async def _trashed(self, team_id):
        return [row]

    monkeypatch.setattr(r.CanvasRepository, "list_trashed_for_team", _trashed)
    resp = await client.get(f"/api/v1/canvases/team/{TEAM_ID}/trash")
    expected = {
        "id": str(row["id"]),
        "name": row["name"],
        "kind": row["kind"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
        "project_id": str(row["project_id"]),
        "project_name": "Alpha",
    }
    assert_wire_unchanged(resp, {"success": True, "data": [expected]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("DELETE", "/api/v1/canvases/5"),
        ("POST", "/api/v1/canvases/5/restore"),
        ("DELETE", "/api/v1/canvases/5/purge"),
    ],
)
async def test_acks_wire_unchanged(client, service, method, path) -> None:
    resp = await client.request(method, path)
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
async def test_asset_refs_wire_unchanged(client, service, monkeypatch) -> None:
    items = [
        {
            "asset_id": "7300000000000000501",
            "node_id": "node-a",
            "loadout_id": "7300000000000000601",
            "asset_name": "Hero",
            "asset_type": "character",
            "loadout_name": "Winter",
        },
        {
            "asset_id": "7300000000000000502",
            "node_id": "node-b",
            "loadout_id": None,
            "asset_name": "Street",
            "asset_type": "location",
            "loadout_name": None,
        },
    ]
    assert set(CanvasAssetRef.model_fields) == set(items[0])

    async def _list(self, canvas_id):
        return items

    monkeypatch.setattr(r.CanvasAssetRefsRepository, "list_for_canvas", _list)
    resp = await client.get("/api/v1/canvases/5/asset-refs")
    assert_wire_unchanged(resp, {"success": True, "data": items, "count": 2})


@pytest.mark.asyncio
async def test_asset_refs_model_matches_the_repository_select(monkeypatch) -> None:
    refs_mod = sys.modules["app.repositories.canvas_asset_refs_repository"]
    sink: list = []

    @asynccontextmanager
    async def _scope():
        yield _CapturingSession(sink)

    monkeypatch.setattr(refs_mod, "read_scope", _scope)
    await refs_mod.CanvasAssetRefsRepository().list_for_canvas("1")
    (stmt,) = sink
    assert set(CanvasAssetRef.model_fields) == {c.key for c in stmt.selected_columns}


# --------------------------------------------------------------------------- #
# Model pickers
# --------------------------------------------------------------------------- #


def _platform_model(name: str, provider: str, **over: Any):
    from app.services.ai.platform_provider import PlatformModel

    fields: Dict[str, Any] = {
        "id": 1900000000000000001,
        "name": name,
        "display_name": name,
        "actual_model": name,
        "type": "image",
        "status": "ok",
        "is_local": False,
        "actual_provider": provider,
        "pricing_type": "per_call",
        "pricing_value": 0.0,
        "context_window_tokens": None,
        "generatable": True,
        "sort_order": 0,
        "last_tested_at": None,
        "last_test_code": None,
    }
    fields.update(over)
    return PlatformModel(**fields)


@pytest.mark.asyncio
async def test_generation_capabilities_wire_unchanged(client, monkeypatch) -> None:
    from types import SimpleNamespace

    import app.services.ai.provider_protocols as protocols
    from app.services.ai.provider_protocols.base import ProviderCapabilities

    caps = ProviderCapabilities(
        ratios=frozenset({"1:1", "16:9"}),
        quality=True,
        quality_tiers=frozenset({"high", "low"}),
        resolution=True,
        max_refs=4,
        negative=True,
        video_modes=frozenset({"t2v", "i2v"}),
        honours_ratio="native",
    )
    import app.services.ai.platform_provider as pp

    view = pp.PlatformProviderView(
        enabled=True,
        models=(
            _platform_model("capable", "capable"),
            _platform_model("unknown", "nobody"),
        ),
        enabled_models=("capable", "unknown"),
        disabled_models=(),
        engine=None,
    )

    def _resolve(provider):
        return SimpleNamespace(capabilities=caps) if provider == "capable" else None

    async def _view(user_id, **_):
        return view

    monkeypatch.setattr(pp, "platform_provider_view", _view)
    monkeypatch.setattr(protocols, "resolve_generation_protocol", _resolve)
    resp = await client.get("/api/v1/canvases/generation-capabilities")
    expected = {
        "capable": {
            "ratios": ["16:9", "1:1"],
            "quality": True,
            "quality_tiers": ["low", "high"],
            "resolution": True,
            "max_refs": 4,
            "negative": True,
            "video_modes": ["i2v", "t2v"],
        },
        "unknown": {
            "ratios": [],
            "quality": False,
            "quality_tiers": [],
            "resolution": False,
            "max_refs": 0,
            "negative": False,
            "video_modes": [],
        },
    }
    assert set(CanvasGenerationCapability.model_fields) == set(expected["capable"])
    assert_wire_unchanged(resp, {"success": True, "data": expected})
