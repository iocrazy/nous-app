"""HTTP tests for POST /api/v1/canvases/runs/classic-node (Phase 5a path B).

Hermetic: overrides the auth dependency, stubs canvas→project gating (no DB),
and patches the storyboard image service so generate_image never hits a real
provider. Exercises the full FastAPI routing + Pydantic serialization for the
new server-resolved ClassicMode node route.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.services.canvas.canvas_run_service import CanvasRunService

# NOTE: ``app.api.__init__`` rebinds the name ``canvases_router`` to the
# APIRouter instance, so ``import app.api.canvases_router as canvases_router``
# would yield the router, not the module. Grab the real module from sys.modules.
canvases_router = sys.modules["app.api.canvases_router"]

URL = "/api/v1/canvases/runs/classic-node"
FAKE_USER_ID = str(uuid4())
FAKE_PROJECT_ID = "777"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture(autouse=True)
def _bypass_gating(monkeypatch):
    """Stub canvas→project gating so the route runs without a DB."""

    async def _fake_gate(canvas_id: str, auth) -> str:
        return FAKE_PROJECT_ID

    monkeypatch.setattr(canvases_router, "_gate_canvas_write", _fake_gate)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_image_gen_node_returns_image_url_envelope(client, monkeypatch):
    gen = AsyncMock(
        return_value={
            "image_url": "https://cdn/result.png",
            "width": 1024,
            "height": 1024,
        }
    )
    monkeypatch.setattr(
        CanvasRunService,
        "_storyboard_ai_service",
        lambda self: SimpleNamespace(generate_image=gen),
    )

    resp = await client.post(
        URL,
        json={
            "canvas_id": "123",
            "node": {
                "id": "n1",
                "type": "image_gen",
                "data": {
                    "prompt": "a fox",
                    "model": "seedream",
                    "provider_name": "doubao",
                },
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["result"]["image_url"] == "https://cdn/result.png"
    assert data["text"] == "https://cdn/result.png"
    assert data["response_kind"] == "classic_node_run"
    # project_id from gating flowed into generate_image.
    assert gen.await_args.kwargs["project_id"] == FAKE_PROJECT_ID
    assert gen.await_args.kwargs["node_id"] == "n1"


@pytest.mark.asyncio
async def test_video_gen_node_returns_video_url_envelope(client, monkeypatch):
    gen = AsyncMock(
        return_value={
            "video_url": "https://cdn/result.mp4",
            "thumbnail_url": "https://cdn/thumb.png",
            "duration_seconds": 5.0,
            "width": 1024,
            "height": 576,
        }
    )
    monkeypatch.setattr(
        CanvasRunService,
        "_storyboard_ai_service",
        lambda self: SimpleNamespace(generate_video=gen),
    )

    resp = await client.post(
        URL,
        json={
            "canvas_id": "123",
            "node": {
                "id": "n1",
                "type": "video_gen",
                "data": {
                    "source_image_url": "https://cdn/src.png",
                    "prompt": "slow pan",
                    "model": "seedance",
                    "provider_name": "doubao",
                },
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["result"]["video_url"] == "https://cdn/result.mp4"
    assert data["result"]["thumbnail_url"] == "https://cdn/thumb.png"
    assert data["text"] == "https://cdn/result.mp4"
    assert data["response_kind"] == "classic_node_run"
    # project_id from gating flowed into generate_video.
    assert gen.await_args.kwargs["project_id"] == FAKE_PROJECT_ID
    assert gen.await_args.kwargs["node_id"] == "n1"
    assert gen.await_args.kwargs["source_image_url"] == "https://cdn/src.png"


@pytest.mark.asyncio
async def test_unknown_node_type_returns_in_band_error_200(client):
    resp = await client.post(
        URL,
        json={
            "canvas_id": "123",
            "node": {"id": "n1", "type": "frobnicate", "data": {}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "frobnicate" in (data["error"] or "")
    assert data["result"] is None
