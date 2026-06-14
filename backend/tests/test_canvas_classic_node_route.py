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
from app.schemas.canvas_run import CanvasPromptRunResult
from app.services.canvas import nous_center_runner
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
async def test_comfy_node_routes_to_nous_workflow(client, monkeypatch):
    """A comfy node resolves to ``nous/<workflow_slug>`` and block-runs it."""
    run = AsyncMock(
        return_value=CanvasPromptRunResult(ok=True, text="comfy output", error=None)
    )
    monkeypatch.setattr(nous_center_runner, "run_nous_workflow", run)
    # The nous path reads settings before dispatching; stub it (no real config
    # in tests). The mocked workflow ignores the value.
    monkeypatch.setattr(
        CanvasRunService, "_get_settings", AsyncMock(return_value=SimpleNamespace())
    )

    resp = await client.post(
        URL,
        json={
            "canvas_id": "123",
            "node": {
                "id": "n1",
                "type": "comfy",
                "data": {"workflow_slug": "my-wf", "prompt": "render this"},
            },
            "body": "render this",
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["text"] == "comfy output"
    assert data["response_kind"] == "classic_node_run"
    # Dispatched down the nous path with the workflow slug from node.data and
    # the run body as the prompt.
    assert run.await_args.kwargs["workflow_slug"] == "my-wf"
    assert run.await_args.kwargs["prompt"] == "render this"


@pytest.mark.asyncio
async def test_llm_node_routes_to_text_adapter(client, monkeypatch):
    """An llm node resolves to the bare-model text adapter path."""
    adapter = SimpleNamespace(
        call=AsyncMock(
            return_value={"choices": [{"message": {"content": "llm reply"}}]}
        )
    )
    get_adapter = AsyncMock(return_value=adapter)
    monkeypatch.setattr(CanvasRunService, "_get_adapter", get_adapter)

    resp = await client.post(
        URL,
        json={
            "canvas_id": "123",
            "node": {
                "id": "n1",
                "type": "llm",
                "data": {"model": "qwen-plus", "prompt": "say hi"},
            },
            "body": "say hi",
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["text"] == "llm reply"
    assert data["response_kind"] == "classic_node_run"
    # Dispatched down the adapter path: model from node.data, body as the
    # user message.
    assert get_adapter.await_args.args[0] == "qwen-plus"
    sent_messages = adapter.call.await_args.args[1]
    assert sent_messages == [{"role": "user", "content": "say hi"}]


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
