"""HTTP tests for POST /canvases/{id}/timeline-runs (G8 timeline director).

One dispatch = one task_tracking row + one routed DBOS workflow carrying the
validated segments. Payload validation is hard: blank prompts, zero/oversize
segment lists, and out-of-range seconds are rejected at the boundary.
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


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(
        canvases_router, "_gate_canvas_write", AsyncMock(return_value="1")
    )
    manager = SimpleNamespace(create=AsyncMock(return_value="task-1"))
    monkeypatch.setattr(canvases_router, "get_task_manager", lambda: manager)
    start = AsyncMock(return_value={"status": "ENQUEUED"})
    from app.services.infra import dbos_orchestrator

    monkeypatch.setattr(dbos_orchestrator, "start_workflow_routed", start)
    return manager, start


BODY = {
    "node_id": "tl1",
    "segments": [
        {"prompt": "opening shot", "seconds": 5},
        {"prompt": "waves crash", "seconds": 4},
    ],
    "model": "",
    "aspect": "16:9",
}


class TestTimelineDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_one_task_with_segments(self, client, wired):
        manager, start = wired
        resp = await client.post("/api/v1/canvases/5/timeline-runs", json=BODY)
        assert resp.status_code == 200
        assert resp.json()["data"]["task_id"]

        create_kwargs = manager.create.await_args.kwargs
        assert create_kwargs["task_type"] == "canvas_timeline"
        assert create_kwargs["subtitle"] == "opening shot"
        assert create_kwargs["metadata"]["segments_total"] == 2

        wf_kwargs = start.await_args.kwargs["dbos_workflow_kwargs"]
        assert [s["prompt"] for s in wf_kwargs["segments"]] == [
            "opening shot",
            "waves crash",
        ]
        assert wf_kwargs["aspect"] == "16:9"
        assert wf_kwargs["user_id"] == FAKE_USER_ID

    @pytest.mark.asyncio
    async def test_rejects_blank_prompt(self, client, wired):
        bad = {**BODY, "segments": [{"prompt": "   ", "seconds": 5}]}
        resp = await client.post("/api/v1/canvases/5/timeline-runs", json=bad)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_empty_and_oversize_segment_lists(self, client, wired):
        resp = await client.post(
            "/api/v1/canvases/5/timeline-runs", json={**BODY, "segments": []}
        )
        assert resp.status_code == 422
        many = [{"prompt": f"s{i}", "seconds": 3} for i in range(13)]
        resp = await client.post(
            "/api/v1/canvases/5/timeline-runs", json={**BODY, "segments": many}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_seconds(self, client, wired):
        bad = {**BODY, "segments": [{"prompt": "x", "seconds": 99}]}
        resp = await client.post("/api/v1/canvases/5/timeline-runs", json=bad)
        assert resp.status_code == 422
