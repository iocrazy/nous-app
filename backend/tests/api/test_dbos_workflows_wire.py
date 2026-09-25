"""DBOS run routes (``/api/v1/workflows/{id}/…``): wire parity after they
gained response models (P9).

Driven over real HTTP through the full app. DBOS is replaced at the router's
own client-aware seams (``_status_read`` / ``_steps_read`` / ``_cancel`` /
``_resume`` / ``_fork``), so the handler's projection (``_serialize_status``,
``_get_steps``) runs for real on objects carrying every field DBOS returns.
``input`` / ``output`` hold values JSON has no native form for (tuples,
datetimes, dataclasses), which is where a response model could re-serialize
differently from the ``jsonable_encoder`` FastAPI used on the bare dict.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.schemas.workflow_responses import DbosWorkflowSnapshot, DbosWorkflowStep
from tests.api.wire_parity import SAMPLE_TS, assert_wire_unchanged

wr = importlib.import_module("app.api.workflows_router")
access = importlib.import_module("app.api.workflow_access")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
WF = "parse-0123abcd-456789abcdef"
BASE = f"/api/v1/workflows/{WF}"


@dataclasses.dataclass
class _Payload:
    url: str
    when: dt.datetime


def full_status(**overrides: Any) -> SimpleNamespace:
    """Every field of dbos ``WorkflowStatus`` (the router reads a subset)."""
    fields = {
        "workflow_id": WF,
        "status": "SUCCESS",
        "name": "parse_workflow",
        "class_name": None,
        "config_name": None,
        "authenticated_user": USER,
        "assumed_role": None,
        "authenticated_roles": None,
        "input": {
            "args": ("https://example.com/v", 3, SAMPLE_TS),
            "kwargs": {"payload": _Payload("u", SAMPLE_TS), "user_id": USER},
        },
        "output": {"resource_id": 7300000000000000123, "at": SAMPLE_TS},
        "error": None,
        "created_at": 1_790_000_000_123,
        "updated_at": 1_790_000_004_567,
        "queue_name": "parse_queue",
        "executor_id": "worker-1",
        "app_version": "v42",
        "workflow_timeout_ms": None,
        "workflow_deadline_epoch_ms": None,
        "deduplication_id": None,
        "priority": 0,
        "queue_partition_key": None,
        "forked_from": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def null_status() -> SimpleNamespace:
    return full_status(
        status=None,
        name=None,
        queue_name=None,
        created_at=None,
        updated_at=None,
        executor_id=None,
        app_version=None,
        authenticated_user=None,
        input=None,
        output=None,
    )


STEPS = [
    {
        "function_id": 1,
        "function_name": "download_step",
        "output": {"path": "/tmp/x", "at": SAMPLE_TS, "parts": (1, 2)},
        "error": None,
        "child_workflow_id": None,
        "started_at_epoch_ms": 1_790_000_000_200,
        "completed_at_epoch_ms": 1_790_000_001_300,
    },
    {
        "function_id": 2,
        "function_name": "save_step",
        "output": None,
        "error": ValueError("disk full"),
        "child_workflow_id": "child-1",
        "started_at_epoch_ms": None,
        "completed_at_epoch_ms": None,
    },
]


@pytest.fixture
def dbos(monkeypatch) -> SimpleNamespace:
    state = SimpleNamespace(status=full_status(), steps=list(STEPS), calls=[])
    monkeypatch.setattr(wr.dbos_orchestrator, "is_enabled", lambda: True)

    async def status_read(wf):
        return state.status

    async def retrieve_result(wf):
        raise RuntimeError("upstream exploded")

    async def steps_read(wf):
        return state.steps

    async def cancel(wf):
        state.calls.append("cancel")

    async def resume(wf):
        state.calls.append("resume")

    async def fork(wf):
        state.calls.append("fork")
        return SimpleNamespace(workflow_id="forked-1")

    monkeypatch.setattr(wr, "_status_read", status_read)
    monkeypatch.setattr(wr, "_retrieve_result", retrieve_result)
    monkeypatch.setattr(wr, "_steps_read", steps_read)
    monkeypatch.setattr(wr, "_cancel", cancel)
    monkeypatch.setattr(wr, "_resume", resume)
    monkeypatch.setattr(wr, "_fork", fork)

    async def owns(workflow_id, user_id):
        return workflow_id == WF and str(user_id) == USER

    monkeypatch.setattr(access, "caller_owns_workflow", owns)
    return state


@pytest.fixture(autouse=True)
def _auth():
    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_models_cover_every_emitted_key():
    snap = wr._serialize_status(full_status(), include_io=True)
    assert set(DbosWorkflowSnapshot.model_fields) == set(snap)
    assert set(DbosWorkflowStep.model_fields) == set(STEPS[0])


# ── status ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ws",
    [
        full_status(),
        null_status(),
        # ERROR with no error recorded: the route realises the result to
        # surface the exception text.
        full_status(status="ERROR", output=None),
        full_status(status="ERROR", error=KeyError("missing")),
    ],
    ids=["full", "nulls", "error-realised", "error-recorded"],
)
async def test_status(client, dbos, ws):
    dbos.status = ws
    raw = await wr._get_status(WF)
    resp = await client.get(f"{BASE}/status")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_status_keeps_isoformat_inside_input(client, dbos):
    """The case a bare response model gets wrong: a nested datetime."""
    resp = await client.get(f"{BASE}/status")
    assert resp.json()["input"]["args"][2] == SAMPLE_TS.isoformat()
    assert resp.json()["output"]["at"].endswith("+00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "steps"])
async def test_unknown_to_dbos_is_the_typed_404(client, dbos, action):
    dbos.status = None
    resp = await client.get(f"{BASE}/{action}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# ── steps ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", [STEPS, []], ids=["steps", "empty"])
async def test_steps(client, dbos, steps):
    dbos.steps = steps
    raw = {"workflow_id": WF, "steps": await wr._get_steps(WF)}
    resp = await client.get(f"{BASE}/steps")
    assert_wire_unchanged(resp, raw)
    if steps:
        assert resp.json()["steps"][1]["error"] == "disk full"


# ── control ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel(client, dbos):
    resp = await client.post(f"{BASE}/cancel")
    assert_wire_unchanged(resp, {"status": "cancel_requested", "workflow_id": WF})
    assert dbos.calls == ["cancel"]


@pytest.mark.asyncio
async def test_resume(client, dbos):
    resp = await client.post(f"{BASE}/resume")
    assert_wire_unchanged(resp, {"status": "resumed", "workflow_id": WF})
    assert dbos.calls == ["resume"]


@pytest.mark.asyncio
async def test_restart(client, dbos):
    resp = await client.post(f"{BASE}/restart")
    assert_wire_unchanged(
        resp,
        {
            "status": "restarted",
            "original_workflow_id": WF,
            "new_workflow_id": "forked-1",
        },
        status=202,
    )
    assert dbos.calls == ["fork"]


# ── the run list is gone ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runs_list_is_removed(client, monkeypatch):
    """``/runs`` now falls through to the template router, whose id guard
    answers a non-numeric id with the typed 404 before touching the DB."""
    resp = await client.get("/api/v1/workflows/runs")
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"
    assert not any(
        getattr(r, "path", "") == "/workflows/runs" for r in wr.router.routes
    )
