"""Project-assets read routes: wire parity after they gained response models.

The two ref queries hand back native datetimes and text-cast ids; the rows
here carry exactly the columns each query SELECTs (captured from the
statement), in those native types, and the body must equal what FastAPI
sent for the bare dict (``tests/api/wire_parity.py``).
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.schemas.canvas_responses import (
    CanvasReferencedResource,
    ProjectAssetsTreeCanvas,
    ResourceCanvasRef,
)
from tests.api.wire_parity import SAMPLE_TS, assert_wire_unchanged

par = sys.modules["app.api.project_assets_router"]
refs_mod = sys.modules["app.repositories.canvas_refs_repository"]

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


class _Session:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    async def execute(self, stmt):
        self.sink.append(stmt)

        class _Result:
            def mappings(self):
                class _M:
                    def all(self):
                        return []

                return _M()

        return _Result()


async def _selected_columns(monkeypatch, call) -> set:
    sink: list = []

    @asynccontextmanager
    async def _scope():
        yield _Session(sink)

    monkeypatch.setattr(refs_mod, "read_scope", _scope)
    monkeypatch.setattr(refs_mod, "is_enforced", lambda _table: False)
    await call(refs_mod.CanvasRefsRepository())
    return {c.key for c in sink[-1].selected_columns}


@pytest.mark.asyncio
async def test_models_match_the_repository_selects(monkeypatch) -> None:
    assets = await _selected_columns(
        monkeypatch, lambda repo: repo.list_assets_for_canvas("1")
    )
    refs = await _selected_columns(
        monkeypatch, lambda repo: repo.list_canvases_for_resource("1")
    )
    tree = await _selected_columns(
        monkeypatch, lambda repo: repo.tree_for_projects(["1"])
    )
    assert set(CanvasReferencedResource.model_fields) == assets
    assert set(ResourceCanvasRef.model_fields) == refs
    # The router drops node_count (it only filters on it) and regroups by
    # project_id.
    assert set(ProjectAssetsTreeCanvas.model_fields) == tree - {
        "node_count",
        "project_id",
    }


def _asset_row(**overrides):
    row = {
        "id": "7300000000000000111",
        "filename": "a.png",
        "file_type": "image",
        "mime_type": "image/png",
        "thumbnail_path": "thumbs/a.jpg",
        "cover_image_path": "covers/a.jpg",
        "created_at": SAMPLE_TS,
        "role": "reference",
        "node_id": "node-1",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_canvas_assets_wire_unchanged(client, monkeypatch) -> None:
    rows = [
        _asset_row(),
        _asset_row(
            role="output",
            file_type=None,
            mime_type=None,
            thumbnail_path=None,
            cover_image_path=None,
        ),
    ]

    async def _gate(canvas_id, auth):
        return "9000"

    async def _list(self, canvas_id):
        return rows

    monkeypatch.setattr(par, "_gate_canvas_read", _gate)
    monkeypatch.setattr(par.CanvasRefsRepository, "list_assets_for_canvas", _list)
    resp = await client.get("/api/v1/canvases/5001/assets")
    assert_wire_unchanged(resp, {"success": True, "data": rows})
    assert resp.json()["data"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_tree_wire_unchanged(client, monkeypatch) -> None:
    projects = [
        {"id": 7300000000000000001, "name": "Alpha"},
        {"id": 7300000000000000002, "name": "Empty"},
    ]
    rows = [
        {
            "project_id": "7300000000000000001",
            "canvas_id": "7300000000000000501",
            "canvas_name": "Board",
            "kind": "smart",
            "node_count": 4,
            "asset_count": 2,
        },
        {  # blank on both axes: hidden
            "project_id": "7300000000000000001",
            "canvas_id": "7300000000000000502",
            "canvas_name": "Blank",
            "kind": "lite",
            "node_count": 0,
            "asset_count": 0,
        },
    ]

    async def _projects(self, user_id):
        return projects

    async def _tree(self, project_ids):
        return rows

    monkeypatch.setattr(par.ProjectsRepository, "get_user_projects", _projects)
    monkeypatch.setattr(par.CanvasRefsRepository, "tree_for_projects", _tree)
    resp = await client.get("/api/v1/resources/project-assets/tree")
    expected = [
        {
            "project_id": "7300000000000000001",
            "name": "Alpha",
            "canvases": [
                {
                    "canvas_id": "7300000000000000501",
                    "canvas_name": "Board",
                    "kind": "smart",
                    "asset_count": 2,
                }
            ],
        },
        {"project_id": "7300000000000000002", "name": "Empty", "canvases": []},
    ]
    assert_wire_unchanged(resp, {"success": True, "data": expected})


@pytest.mark.asyncio
async def test_resource_canvas_refs_wire_unchanged(client, monkeypatch) -> None:
    rows = [
        {
            "canvas_id": "7300000000000000501",
            "canvas_name": "Board",
            "kind": "character",
            "project_id": "7300000000000000001",
            "role": "output",
        }
    ]

    async def _access(resource_id, user_id, team_id):
        return True

    async def _list(self, resource_id):
        return rows

    monkeypatch.setattr(par, "check_media_access", _access)
    monkeypatch.setattr(par.CanvasRefsRepository, "list_canvases_for_resource", _list)
    resp = await client.get("/api/v1/resources/7300000000000000111/canvas-refs")
    assert_wire_unchanged(resp, {"success": True, "data": rows, "count": 1})
