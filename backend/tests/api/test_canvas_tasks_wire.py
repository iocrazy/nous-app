"""Canvas task routes: wire parity after they gained response models (P4).

Covers generation poll / cancel / dispatch, timeline dispatch, smart-mode
prompt runs, the three derive endpoints and the Download-All zip. Every JSON
route is driven over real HTTP and its body must equal ``jsonable_encoder``
of the dict the handler builds (``tests/api/wire_parity.py``). The poll row
is built from the ``TaskTracking`` ORM columns the route SELECTs, and the
model's field set is pinned to that SELECT (captured, not retyped).
"""

from __future__ import annotations

import io
import sys
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import TaskTracking
from app.schemas.canvas_run import CanvasPromptRunResponse, CanvasPromptRunResult
from app.schemas.canvas_task_responses import CanvasGenerationTask
from app.services.canvas import canvas_derive_service as cds
from tests.api.wire_parity import assert_wire_unchanged, sample_row

r = sys.modules["app.api.canvases_router"]
derive_router = sys.modules["app.api.canvas_derive_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
CANVAS_ID = "7300000000000000123"
TASK_COLUMNS = ("dbos_workflow_id", "phase", "status", "error_msg", "metadata")


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _gates(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _gate(canvas_id, auth):
        return "7300000000000000001"

    monkeypatch.setattr(r, "_gate_canvas_write", _gate)
    monkeypatch.setattr(derive_router, "_gate_canvas_write", _gate)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _read_scope(row: dict | None, sink: list | None = None):
    class _Res:
        def mappings(self):
            return self

        def first(self):
            return row

        def scalar(self):
            return row.get("dbos_workflow_id") if row else None

    class _Session:
        async def execute(self, stmt):
            if sink is not None:
                sink.append(stmt)
            return _Res()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


def _task_row(**overrides: Any) -> dict:
    row = sample_row(TaskTracking, only=TASK_COLUMNS)
    row["metadata"] = {
        "result_url": "/api/v1/generated-media/7300000000000000777/cover",
        "generated_media_id": 7_300_000_000_000_000_777,
        "dropped_knobs": [],
        "dropped_refs": [{"url": "/x", "reason": "unresolvable"}],
        "failure": {"code": "refused", "detail": "why"},
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- #
# Generation poll / cancel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_poll_model_matches_the_route_select(client, monkeypatch) -> None:
    import app.db.session as db_session

    sink: list = []
    monkeypatch.setattr(db_session, "read_scope", _read_scope(_task_row(), sink))
    await client.get("/api/v1/canvases/generations/t-1")
    (stmt,) = sink
    assert {c.key for c in stmt.selected_columns} == set(
        CanvasGenerationTask.model_fields
    )
    assert set(TASK_COLUMNS) == set(CanvasGenerationTask.model_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        # A just-queued row: no phase yet, no error, no decoration.
        {"phase": None, "error_msg": None, "metadata": None},
    ],
)
async def test_poll_wire_unchanged(client, monkeypatch, overrides) -> None:
    import app.db.session as db_session

    row = _task_row(**overrides)
    monkeypatch.setattr(db_session, "read_scope", _read_scope(row))
    resp = await client.get("/api/v1/canvases/generations/t-1")
    assert_wire_unchanged(resp, {"success": True, "data": dict(row)})


@pytest.mark.asyncio
async def test_cancel_wire_unchanged(client, monkeypatch) -> None:
    import app.db.session as db_session
    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(db_session, "read_scope", _read_scope(_task_row()))
    monkeypatch.setattr(orch, "cancel_workflow", AsyncMock(), raising=False)
    resp = await client.delete("/api/v1/canvases/generations/t-1")
    assert_wire_unchanged(resp, {"success": True})


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


@pytest.fixture
def dispatch(monkeypatch):
    ids = iter(f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, 10))

    async def _create(**kwargs):
        return next(ids)

    mgr = SimpleNamespace(
        create=AsyncMock(side_effect=_create),
        create_flow=AsyncMock(return_value="11111111-1111-1111-1111-111111111111"),
    )
    monkeypatch.setattr(r, "get_task_manager", lambda: mgr)
    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock())
    return mgr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "flow_id"),
    [(2, "11111111-1111-1111-1111-111111111111"), (1, None)],
)
async def test_dispatch_generations_wire_unchanged(
    client, dispatch, count, flow_id
) -> None:
    resp = await client.post(
        f"/api/v1/canvases/{CANVAS_ID}/generations",
        json={"node_id": "n1", "kind": "image", "prompt": "a cat", "count": count},
    )
    task_ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, count + 1)]
    assert_wire_unchanged(
        resp, {"success": True, "task_ids": task_ids, "flow_id": flow_id}
    )


@pytest.mark.asyncio
async def test_dispatch_generations_flow_insert_failed_keeps_null(
    client, dispatch
) -> None:
    dispatch.create_flow = AsyncMock(return_value=None)
    resp = await client.post(
        f"/api/v1/canvases/{CANVAS_ID}/generations",
        json={"node_id": "n1", "kind": "image", "prompt": "a cat", "count": 2},
    )
    assert resp.json()["flow_id"] is None
    assert len(resp.json()["task_ids"]) == 2


@pytest.mark.asyncio
async def test_timeline_dispatch_wire_unchanged(client, dispatch, monkeypatch) -> None:
    import uuid as uuid_mod

    fixed = uuid_mod.UUID("22222222-2222-2222-2222-222222222222")
    monkeypatch.setattr(uuid_mod, "uuid4", lambda: fixed)
    resp = await client.post(
        f"/api/v1/canvases/{CANVAS_ID}/timeline-runs",
        json={"node_id": "n1", "segments": [{"prompt": "open", "seconds": 5}]},
    )
    assert_wire_unchanged(resp, {"success": True, "data": {"task_id": str(fixed)}})


# --------------------------------------------------------------------------- #
# Prompt runs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        CanvasPromptRunResult(ok=True, text="hello", result={"image_url": "/x"}),
        CanvasPromptRunResult(ok=False, error="adapter init failed"),
    ],
)
async def test_prompt_run_wire_unchanged(client, monkeypatch, result) -> None:
    class _Svc:
        async def run_prompt(self, **kwargs):
            return result

    monkeypatch.setattr(r, "CanvasRunService", _Svc)
    resp = await client.post(
        "/api/v1/canvases/runs/prompts",
        json={"canvas_id": CANVAS_ID, "prompt_node_id": "p1", "body": "hi"},
    )
    # The route builds the reply from ok/text/error only: ``result`` is never
    # forwarded, so it is always null on the wire.
    body = CanvasPromptRunResponse(ok=result.ok, text=result.text, error=result.error)
    assert_wire_unchanged(resp, {"success": True, "data": body.model_dump(mode="json")})
    assert resp.json()["data"]["response_kind"] == "canvas_prompt_run"


# --------------------------------------------------------------------------- #
# Derive
# --------------------------------------------------------------------------- #


def _image(gen_id: str, row: int | None = None, col: int | None = None):
    return cds.CanvasDerivedImage(
        id=gen_id, url=f"/api/v1/generated-media/{gen_id}/cover", row=row, col=col
    )


def _derive_body(images) -> dict:
    return {
        "success": True,
        "data": {
            "images": [
                {"id": i.id, "url": i.url, "kind": "image", "row": i.row, "col": i.col}
                for i in images
            ]
        },
    }


@pytest.mark.asyncio
async def test_derive_crop_wire_unchanged(client, monkeypatch) -> None:
    image = _image("7300000000000000901")

    async def _crop(**kwargs):
        return image

    monkeypatch.setattr(cds, "derive_canvas_crop", _crop)
    resp = await client.post(
        f"/api/v1/canvases/{CANVAS_ID}/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 1},
        },
    )
    assert_wire_unchanged(resp, _derive_body([image]))


@pytest.mark.asyncio
async def test_derive_grid_wire_unchanged(client, monkeypatch) -> None:
    images = [
        _image("7300000000000000901", 0, 0),
        _image("7300000000000000902", 0, 1),
    ]

    async def _grid(**kwargs):
        return images

    monkeypatch.setattr(cds, "derive_canvas_grid", _grid)
    resp = await client.post(
        f"/api/v1/canvases/{CANVAS_ID}/derive-grid",
        json={"source_url": "/api/v1/generated-media/5/cover", "xs": [0.5], "ys": []},
    )
    assert_wire_unchanged(resp, _derive_body(images))


@pytest.mark.asyncio
async def test_derive_outpaint_wire_unchanged(client, monkeypatch) -> None:
    image = _image("7300000000000000903")

    async def _outpaint(**kwargs):
        return image

    monkeypatch.setattr(cds, "derive_canvas_outpaint", _outpaint)
    resp = await client.post(
        f"/api/v1/canvases/{CANVAS_ID}/derive-outpaint",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "left": 0.1,
            "top": 0,
            "right": 0,
            "bottom": 0,
        },
    )
    assert_wire_unchanged(resp, _derive_body([image]))


# --------------------------------------------------------------------------- #
# Download-All zip (bytes, not JSON)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_zip_returns_archive_bytes(client, monkeypatch) -> None:
    import app.repositories.generated_media_repository as gm_repo
    import app.services.library.resources_service as res_svc

    monkeypatch.setattr(
        res_svc, "_resolve_personal_team_id", AsyncMock(return_value="9")
    )
    monkeypatch.setattr(
        gm_repo.GeneratedMediaRepository,
        "get",
        AsyncMock(return_value={"file_path": "x.png"}),
    )
    monkeypatch.setattr(r, "_read_media_bytes", AsyncMock(return_value=b"PNGDATA"))
    resp = await client.post(
        "/api/v1/canvases/assets/zip",
        json={
            "filename": "shots",
            "items": [{"url": "/api/v1/generated-media/5/file", "name": "a.png"}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == 'attachment; filename="shots.zip"'
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert archive.read("a.png") == b"PNGDATA"


def test_zip_contract_is_binary_not_json() -> None:
    op = app.openapi()["paths"]["/api/v1/canvases/assets/zip"]["post"]
    assert set(op["responses"]["200"]["content"]) == {"application/zip"}
