"""HTTP tests for the canvas generation endpoints (G4-B1).

POST /api/v1/canvases/{canvas_id}/generations — dispatch count× DBOS tasks
GET  /api/v1/canvases/generation-models      — image/video rows for the composer
GET  /api/v1/canvases/generations/{task_id}  — poll one task (task_tracking row)

Hermetic: auth overridden, canvas→project gating stubbed, task manager and
workflow dispatch mocked — no DB, no DBOS.
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

canvases_router = sys.modules["app.api.canvases_router"]

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
    async def _fake_gate(canvas_id: str, auth) -> str:
        return FAKE_PROJECT_ID

    monkeypatch.setattr(canvases_router, "_gate_canvas_write", _fake_gate)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
def dispatch(monkeypatch):
    """Mock task-manager create + workflow dispatch; returns the mocks."""
    created: list[dict] = []

    async def _create(**kwargs):
        created.append(kwargs)
        return kwargs.get("dbos_workflow_id") or str(uuid4())

    mgr = SimpleNamespace(create=AsyncMock(side_effect=_create))
    monkeypatch.setattr(canvases_router, "get_task_manager", lambda: mgr)

    started = AsyncMock(return_value={"ok": True})
    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", started)
    return SimpleNamespace(mgr=mgr, started=started, created=created)


class TestPostGenerations:
    @pytest.mark.asyncio
    async def test_image_count_fans_out_one_task_per_item(self, client, dispatch):
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={
                "node_id": "n1",
                "kind": "image",
                "prompt": "a cat",
                "count": 3,
                "params": {"ratio": "16:9"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["task_ids"]) == 3
        assert dispatch.started.await_count == 3
        kw = dispatch.started.await_args.kwargs["dbos_workflow_kwargs"]
        assert kw["kind"] == "image"
        assert kw["prompt"] == "a cat"
        assert kw["canvas_id"] == 123
        assert kw["node_id"] == "n1"
        assert kw["user_id"] == FAKE_USER_ID
        assert kw["params"] == {"ratio": "16:9"}
        # task rows carry the business decoration for the composer UI
        assert dispatch.created[0]["task_type"] == "canvas_gen"
        assert dispatch.created[0]["metadata"]["node_id"] == "n1"

    @pytest.mark.asyncio
    async def test_count_is_clamped_to_eight(self, client, dispatch):
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={"node_id": "n1", "kind": "image", "prompt": "p", "count": 99},
        )
        assert resp.status_code == 200
        assert len(resp.json()["task_ids"]) == 8

    @pytest.mark.asyncio
    async def test_video_forces_single_task(self, client, dispatch):
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={"node_id": "n1", "kind": "video", "prompt": "p", "count": 5},
        )
        assert resp.status_code == 200
        assert len(resp.json()["task_ids"]) == 1

    @pytest.mark.asyncio
    async def test_blank_prompt_is_rejected(self, client, dispatch):
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={"node_id": "n1", "kind": "image", "prompt": "   "},
        )
        assert resp.status_code == 422
        assert dispatch.started.await_count == 0


class TestGenerationModels:
    @pytest.mark.asyncio
    async def test_lists_only_image_and_video_public_fields(self, client, monkeypatch):
        rows = [
            {
                "name": "jimeng-cli-image",
                "display_name": "Jimeng",
                "type": "image",
                "actual_provider": "jimeng-cli",
                "is_enabled": True,
                "sort_order": 10,
            },
            {
                "name": "jimeng-cli-seedance",
                "display_name": "Seedance",
                "type": "video",
                "actual_provider": "jimeng-cli",
                "is_enabled": True,
                "sort_order": 10,
            },
            {
                "name": "qwen-plus",
                "display_name": "Qwen",
                "type": "llm",
                "actual_provider": "qwen",
                "is_enabled": True,
                "sort_order": 1,
            },
        ]
        repo = SimpleNamespace(list_enabled=AsyncMock(return_value=rows))
        import app.repositories.mediahub_model_repository as repo_mod

        monkeypatch.setattr(repo_mod, "get_mediahub_model_repository", lambda: repo)

        resp = await client.get("/api/v1/canvases/generation-models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        names = [m["name"] for m in data]
        assert names == ["jimeng-cli-image", "jimeng-cli-seedance"]
        assert all("api_key" not in m for m in data)
        assert data[0]["type"] == "image"


class TestGetGenerationStatus:
    @pytest.mark.asyncio
    async def test_returns_task_row_fields(self, client, monkeypatch):
        row = {
            "dbos_workflow_id": "task-1",
            "phase": "completed",
            "status": "completed",
            "error_msg": None,
            "metadata": {
                "result_url": "/api/v1/generated-media/5/cover",
                "kind": "image",
            },
        }

        class _Q:
            def table(self, *_a):
                return self

            def select(self, *_a):
                return self

            def eq(self, *_a):
                return self

            def single(self):
                return self

            async def execute(self):
                return SimpleNamespace(data=row)

        mgr = SimpleNamespace(_get_client=AsyncMock(return_value=_Q()))
        monkeypatch.setattr(canvases_router, "get_task_manager", lambda: mgr)

        resp = await client.get("/api/v1/canvases/generations/task-1")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["phase"] == "completed"
        assert data["metadata"]["result_url"] == "/api/v1/generated-media/5/cover"
