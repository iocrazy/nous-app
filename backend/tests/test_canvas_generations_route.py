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
def _no_readiness_filter(monkeypatch):
    """The picker now offers only what can generate right now (2026-09-05,
    local_readiness). These tests are about the endpoints' projection, not
    that rule, so make the filter the identity here;
    test_generation_picker_readiness covers the rule itself."""
    from app.services.generation import local_readiness as lr

    monkeypatch.setattr(lr, "apply_readiness", lambda rows, _ready: rows)
    monkeypatch.setattr(
        lr, "local_engine_readiness", AsyncMock(return_value=lr.LocalReadiness())
    )


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

    # One flow per submission when count > 1 (2026-09-05): `create_flow`
    # hands back the parent task_flows id that every child row links to.
    flows: list[dict] = []
    flow_id = "flow-" + str(uuid4())

    async def _create_flow(user_id, name, *, metadata=None):
        flows.append({"user_id": user_id, "name": name, "metadata": metadata})
        return flow_id

    mgr = SimpleNamespace(
        create=AsyncMock(side_effect=_create),
        create_flow=AsyncMock(side_effect=_create_flow),
    )
    monkeypatch.setattr(canvases_router, "get_task_manager", lambda: mgr)

    started = AsyncMock(return_value={"ok": True})
    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", started)
    return SimpleNamespace(
        mgr=mgr, started=started, created=created, flows=flows, flow_id=flow_id
    )


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
        # This test is about the type/field projection; the Settings platform
        # gate has its own tests — let everything through here.
        async def _allow_all(user_id):
            return True, frozenset()

        monkeypatch.setattr(
            "app.services.ai.platform_model_visibility.platform_model_gate", _allow_all
        )
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


class TestTextModels:
    @pytest.mark.asyncio
    async def test_lists_enabled_llm_public_fields_only(self, client, monkeypatch):
        # The repo does the type filter (list_enabled("llm")); the endpoint
        # only strips to public columns. Mirror that: the mock returns llm rows
        # and we assert no secret columns leak and the type-filter arg is used.
        llm_rows = [
            {
                "name": "mediahub-doubao-llm",
                "display_name": "Doubao LLM",
                "type": "llm",
                "actual_provider": "doubao",
                "is_enabled": True,
                "sort_order": 1,
                "api_key": "platform-secret",
                "base_url": "https://ark.example.com/v1",
            },
        ]
        list_enabled = AsyncMock(return_value=llm_rows)
        repo = SimpleNamespace(list_enabled=list_enabled)
        import app.repositories.mediahub_model_repository as repo_mod

        monkeypatch.setattr(repo_mod, "get_mediahub_model_repository", lambda: repo)

        resp = await client.get("/api/v1/canvases/text-models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [m["name"] for m in data] == ["mediahub-doubao-llm"]
        assert data[0]["type"] == "llm"
        # Public columns only — credentials must never reach the browser.
        assert all("api_key" not in m and "base_url" not in m for m in data)
        assert list_enabled.await_args.args == ("llm",)
        assert list_enabled.await_args.kwargs["viewer_user_id"]


def _read_scope_returning(row, *, forbid_writes: bool = False):
    """A read_scope() stand-in whose session returns ``row``; the endpoints
    run their real ORM statement construction against it. With
    ``forbid_writes`` the session explodes on any UPDATE/INSERT/DELETE —
    proving the cancel endpoint never PATCHes task_tracking (route C)."""
    from contextlib import asynccontextmanager

    class _Res:
        def mappings(self):
            return self

        def first(self):
            return row

        def scalar(self):
            return row.get("dbos_workflow_id") if row else None

    class _Session:
        async def execute(self, stmt):
            if forbid_writes and not str(stmt).lstrip().upper().startswith("SELECT"):
                raise AssertionError(
                    "cancel endpoint must not PATCH task_tracking"
                )  # pragma: no cover - must never run
            return _Res()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


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
        import app.db.session as db_session_mod

        monkeypatch.setattr(db_session_mod, "read_scope", _read_scope_returning(row))

        resp = await client.get("/api/v1/canvases/generations/task-1")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["phase"] == "completed"
        assert data["metadata"]["result_url"] == "/api/v1/generated-media/5/cover"


class TestCancelGeneration:
    """DELETE /canvases/generations/{task_id} — real backend cancel (P1-1).

    Stop used to only abandon the frontend poll while the DBOS task kept
    burning provider quota. The endpoint asks the DBOS engine to cancel; the
    mirror trigger reflects task_tracking.phase='cancelled' — route C, so the
    endpoint MUST NOT write phase itself.
    """

    @pytest.mark.asyncio
    async def test_owner_cancel_calls_dbos_engine_and_skips_phase_patch(
        self, client, monkeypatch
    ):
        row = {"dbos_workflow_id": "task-1", "phase": "processing"}
        import app.db.session as db_session_mod

        monkeypatch.setattr(
            db_session_mod,
            "read_scope",
            _read_scope_returning(row, forbid_writes=True),
        )

        cancel = AsyncMock()
        import app.services.infra.dbos_orchestrator as dbos_orch

        monkeypatch.setattr(dbos_orch, "cancel_workflow", cancel, raising=False)

        resp = await client.delete("/api/v1/canvases/generations/task-1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        cancel.assert_awaited_once_with("task-1")

    @pytest.mark.asyncio
    async def test_unknown_or_unowned_task_is_404(self, client, monkeypatch):
        import app.db.session as db_session_mod

        monkeypatch.setattr(db_session_mod, "read_scope", _read_scope_returning(None))

        cancel = AsyncMock()
        import app.services.infra.dbos_orchestrator as dbos_orch

        monkeypatch.setattr(dbos_orch, "cancel_workflow", cancel, raising=False)

        resp = await client.delete("/api/v1/canvases/generations/nope")
        assert resp.status_code == 404
        cancel.assert_not_awaited()


class TestGenerationModelsFollowSettings:
    """The picker must show what Settings → platform-model card shows."""

    @pytest.mark.asyncio
    async def test_models_the_user_switched_off_in_settings_are_not_listed(
        self, client, monkeypatch
    ):
        from app.repositories import mediahub_model_repository as repo_mod

        rows = [
            {"name": "codex-image", "display_name": "GPT Image", "type": "image"},
            {"name": "jimeng-cli-image", "display_name": "Dreamina", "type": "image"},
        ]
        repo = SimpleNamespace(list_enabled=AsyncMock(return_value=rows))
        monkeypatch.setattr(repo_mod, "get_mediahub_model_repository", lambda: repo)

        async def _gate(user_id):
            return True, frozenset({"codex-image"})

        monkeypatch.setattr(
            "app.services.ai.platform_model_visibility.platform_model_gate", _gate
        )

        resp = await client.get("/api/v1/canvases/generation-models")

        assert resp.status_code == 200
        assert [m["name"] for m in resp.json()["data"]] == ["jimeng-cli-image"]


class TestGenerationsAreOneFlow:
    """ "Generate 3 images" is ONE thing the user asked for. Before this, the
    Task Center showed three unrelated "Generate image" rows for it, while an
    agent run of five steps showed as one card with five dots — the grouping
    the user pointed at on 2026-09-05. task_flows + flow_id already exist
    (migration 203, FlowGroupCard); the dispatcher just never used them."""

    @pytest.mark.asyncio
    async def test_count_gt_one_links_every_task_to_one_flow(self, client, dispatch):
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={"node_id": "n1", "kind": "image", "prompt": "a cat", "count": 3},
        )
        assert resp.status_code == 200
        assert dispatch.mgr.create_flow.await_count == 1
        flow = dispatch.flows[0]
        assert flow["user_id"] == FAKE_USER_ID
        assert flow["name"]  # a human title, never blank
        assert flow["metadata"]["canvas_id"] == "123"
        assert flow["metadata"]["node_id"] == "n1"
        assert flow["metadata"]["count"] == 3
        assert [c.get("flow_id") for c in dispatch.created] == [dispatch.flow_id] * 3
        assert resp.json()["flow_id"] == dispatch.flow_id

    @pytest.mark.asyncio
    async def test_a_single_image_stays_a_single_row(self, client, dispatch):
        """A one-task flow would render as a group card wrapping one row —
        noise, and not what "one submission = one task" means."""
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={"node_id": "n1", "kind": "image", "prompt": "a cat", "count": 1},
        )
        assert resp.status_code == 200
        assert dispatch.mgr.create_flow.await_count == 0
        assert dispatch.created[0].get("flow_id") is None
        assert resp.json()["flow_id"] is None

    @pytest.mark.asyncio
    async def test_flow_creation_failure_never_blocks_dispatch(self, client, dispatch):
        """create_flow is best-effort (it returns None on failure). Grouping is
        presentation; three ungrouped tasks beat zero tasks."""
        dispatch.mgr.create_flow = AsyncMock(return_value=None)
        resp = await client.post(
            "/api/v1/canvases/123/generations",
            json={"node_id": "n1", "kind": "image", "prompt": "a cat", "count": 3},
        )
        assert resp.status_code == 200
        assert dispatch.started.await_count == 3
        assert all(c.get("flow_id") is None for c in dispatch.created)
