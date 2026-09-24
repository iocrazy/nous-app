"""Project-assets read route: wire parity after it gained a response model.

The ref query hands back text-cast ids; the rows here carry exactly the
columns it SELECTs (captured from the statement), and the body must equal
what FastAPI sent for the bare dict (``tests/api/wire_parity.py``).
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.schemas.canvas_responses import ResourceCanvasRef
from tests.api.wire_parity import assert_wire_unchanged

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
    await call(refs_mod.CanvasRefsRepository())
    return {c.key for c in sink[-1].selected_columns}


@pytest.mark.asyncio
async def test_model_matches_the_repository_select(monkeypatch) -> None:
    refs = await _selected_columns(
        monkeypatch, lambda repo: repo.list_canvases_for_resource("1")
    )
    assert set(ResourceCanvasRef.model_fields) == refs


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
