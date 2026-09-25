"""P9 issues / flows / schedules routes: wire parity after they gained models.

Every route is driven over real HTTP, then the same handler is called
directly with identical inputs; the HTTP body must equal ``jsonable_encoder``
of what the handler returned — what FastAPI sent before the model existed.
Rows carry every column with its native type (``tests/api/wire_parity.py``),
and a second variant nulls every nullable column.
"""

from __future__ import annotations

import datetime as dt
import importlib
from contextlib import asynccontextmanager
from typing import Any, Callable
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.encoders import jsonable_encoder
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import UserSchedules
from app.schemas.pipeline import PipelineRun, PipelineRunListResponse
from app.services.issues.issue_rollup import compute_rollup
from tests.api.wire_parity import SAMPLE_TS, sample_row

issues = importlib.import_module("app.api.issues_router")
progress = importlib.import_module("app.api.issue_progress_router")
pipelines = importlib.import_module("app.api.pipelines_router")
flows = importlib.import_module("app.api.flows_router")
schedules = importlib.import_module("app.api.schedules_router")
workflows = importlib.import_module("app.api.workflows_router")
agent_framework = importlib.import_module("app.agent_framework")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
AUTH = AuthContext(user_id=USER, auth_type="jwt")
ISSUE = {"id": 7300000000000000123, "status": "in_progress", "team_id": 1}


@pytest.fixture(autouse=True)
def _auth_and_gates():
    async def _fake_auth() -> AuthContext:
        return AUTH

    app.dependency_overrides[get_auth] = _fake_auth
    gates = [d.dependency for d in issues.router.dependencies] + [
        d.dependency for d in progress.router.dependencies
    ]
    for gate in gates:
        app.dependency_overrides[gate] = lambda: None
    yield
    app.dependency_overrides.pop(get_auth, None)
    for gate in gates:
        app.dependency_overrides.pop(gate, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class _Rows:
    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> Any:
        return self._rows


def _scopes(monkeypatch, target: Any, make: Callable[[], list[Any]]) -> Callable:
    """Scripted read/write scopes on ``target``; ``reset()`` replays them."""
    state: dict[str, list[Any]] = {}

    def reset() -> None:
        state["q"] = make()

    reset()

    class _Session:
        async def execute(self, stmt: Any, *a, **kw) -> _Rows:
            return _Rows(state["q"].pop(0) if state["q"] else [])

    @asynccontextmanager
    async def _scope(*a, **kw):
        yield _Session()

    monkeypatch.setattr(target, "read_scope", _scope)
    monkeypatch.setattr(target, "write_scope", _scope, raising=False)
    return reset


# --------------------------------------------------------------------------- #
# GET /issues/{id}/progress
# --------------------------------------------------------------------------- #


def _rollup(nulls: bool) -> dict[str, Any]:
    t0 = SAMPLE_TS
    running = {
        "id": 7300000000000000201,
        "status": "running",
        "started_at": t0,
        "ended_at": None,
        "model": None if nulls else "m",
        "error_code": None,
        "cost_cents": None if nulls else 1.5,
        "metadata_json": (
            {}
            if nulls
            else {
                "view": {"phase": "tool", "step": {"n": 3}},
                "cost": {"spent_cents": 2.25},
            }
        ),
    }
    done = {
        "id": 7300000000000000202,
        "status": "completed",
        "started_at": t0 - dt.timedelta(hours=1),
        "ended_at": None if nulls else t0,
        "model": "m",
        "error_code": None if nulls else "e",
        "cost_cents": 3.0,
        "metadata_json": None if nulls else {"view": {"ended": {"reason": "done"}}},
    }
    issue = {
        **ISSUE,
        "paused_at": None if nulls else t0,
        "budget_cents": None if nulls else 500,
        "execution_state": None if nulls else {"turn": 7},
    }
    return compute_rollup(
        issue,
        [running, done],
        [
            {
                "id": 7300000000000000301,
                "identifier": None if nulls else "N-1",
                "title": None if nulls else "t",
                "status": "done",
            }
        ],
        2,
        {"kind": "manual", "origin_id": None},
        spent_cents=12.345678,
        tree_cost_cents={} if nulls else {"7300000000000000202": 4.5},
        now=t0,
        last_seq=None if nulls else 42,
        efficiency=(
            None
            if nulls
            else {
                "runs": 2,
                "steps": 5,
                "tool_calls": 3,
                "tool_errors": 1,
                "deliverables": 2,
                "avg_run_ms": 1200,
                "turn_end_reasons": {"completed": 2},
            }
        ),
        charged_points=None if nulls else {"7300000000000000202": 1.25},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_issue_progress(client, monkeypatch, nulls):
    rollup = _rollup(nulls)

    async def _visible(issue_id: int, auth: Any) -> dict[str, Any]:
        return ISSUE

    async def _load(issue: dict[str, Any]) -> dict[str, Any]:
        return rollup

    monkeypatch.setattr(progress, "assert_issue_visible", _visible)
    monkeypatch.setattr(progress, "load_rollup", _load)
    resp = await client.get(f"/api/v1/issues/{ISSUE['id']}/progress")
    raw = await progress.issue_progress(issue_id=ISSUE["id"], auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)


# --------------------------------------------------------------------------- #
# GET /issues/{id}/schedules and /pipeline-runs
# --------------------------------------------------------------------------- #


@pytest.fixture
def visible_issue(monkeypatch):
    async def _get(issue_id: int) -> dict[str, Any]:
        return ISSUE

    async def _visible(row: dict[str, Any], user_id: str) -> bool:
        return True

    monkeypatch.setattr(issues.issue_repository, "get_by_id", _get)
    monkeypatch.setattr(issues, "is_issue_visible", _visible)


def _schedule_rows(nulls: bool) -> list[dict[str, Any]]:
    wake = sample_row(UserSchedules) | {
        "task_type": "issue_wakeup",
        "payload": {"issue_id": ISSUE["id"], "text": "ping", "created_by": "agent"},
    }
    routine = sample_row(UserSchedules) | {
        "id": UUID(int=77),
        "task_type": "agent_routine",
        "payload": {"last_issue_id": str(ISSUE["id"]), "prompt_md": "go"},
    }
    if nulls:
        nullable = {c.name for c in UserSchedules.__table__.columns if c.nullable}
        wake = {k: (None if k in nullable else v) for k, v in wake.items()}
        # A caller-written payload with an odd created_by must not 500 the list.
        wake["payload"] = {"issue_id": ISSUE["id"], "created_by": 5}
    return [wake, routine]


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_issue_schedules(client, monkeypatch, visible_issue, nulls):
    import app.db.session as db_session

    reset = _scopes(monkeypatch, db_session, lambda: [_schedule_rows(nulls)])
    resp = await client.get(f"/api/v1/issues/{ISSUE['id']}/schedules")
    reset()
    raw = await issues.list_issue_schedules(issue_id=ISSUE["id"], auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
    assert len(resp.json()["items"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_issue_pipeline_runs(client, monkeypatch, visible_issue, nulls):
    row = {
        "id": "9",
        "pipeline_id": "3",
        "parent_issue_id": str(ISSUE["id"]),
        "current_step": 1,
        "status": "running",
        "halted_reason": None if nulls else "h",
        "started_by_user_id": None if nulls else USER,
        "created_at": SAMPLE_TS,
        "updated_at": SAMPLE_TS,
        "completed_at": None if nulls else SAMPLE_TS,
    }

    class _Repo:
        async def list_runs_for_parent(self, issue_id: int) -> list[dict[str, Any]]:
            return [dict(row)]

    async def _enrich(run: dict[str, Any]) -> dict[str, Any]:
        if nulls:
            return run
        return {**run, "pipeline_name": "p", "total_steps": 2, "current_agent_id": "a"}

    import app.repositories.pipeline_repository as pr

    monkeypatch.setattr(pr, "pipeline_repository", _Repo())
    monkeypatch.setattr(pipelines, "_enrich_run", _enrich)
    resp = await client.get(f"/api/v1/issues/{ISSUE['id']}/pipeline-runs")
    raw = await issues.list_issue_pipeline_runs(issue_id=ISSUE["id"], auth=AUTH)
    assert isinstance(raw, PipelineRunListResponse)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)
    assert set(resp.json()["items"][0]) == set(PipelineRun.model_fields)


# --------------------------------------------------------------------------- #
# POST /flows/{id}/cancel
# --------------------------------------------------------------------------- #

FLOW = UUID(int=0xF1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flow,children",
    [
        ({"id": FLOW, "state": "completed", "cascade_cancel": True}, []),
        ({"id": FLOW, "state": "running", "cascade_cancel": False}, []),
        (
            {"id": FLOW, "state": "running", "cascade_cancel": True},
            [{"dbos_workflow_id": "wf-a", "phase": "processing"}],
        ),
    ],
)
async def test_flow_cancel(client, monkeypatch, flow, children):
    async def _cancel(workflow_id: str) -> None:
        return None

    async def _kill(wf_id: str, grace_seconds: float = 0) -> int:
        return 0

    monkeypatch.setattr(workflows, "_cancel", _cancel)
    monkeypatch.setattr(agent_framework, "cancel_workflow_subprocesses", _kill)

    def make() -> list[Any]:
        # flow lookup, flow UPDATE, children, child decoration UPDATE
        return [[flow], [], children, []]

    reset = _scopes(monkeypatch, flows, make)
    resp = await client.post(f"/api/v1/flows/{FLOW}/cancel")
    reset()
    raw = await flows.cancel_flow(flow_id=FLOW, auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)


@pytest.mark.asyncio
async def test_flow_cancel_of_someone_elses_flow_is_404(client, monkeypatch):
    _scopes(monkeypatch, flows, lambda: [[]])
    resp = await client.post(f"/api/v1/flows/{FLOW}/cancel")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_flow_delete_route_is_gone(client):
    resp = await client.delete(f"/api/v1/flows/{FLOW}")
    assert resp.status_code == 405, resp.text


# --------------------------------------------------------------------------- #
# DELETE /schedules/{id}, POST /schedules/{id}/fire-now
# --------------------------------------------------------------------------- #

SID = UUID(int=0x5C)


@pytest.mark.asyncio
async def test_schedule_delete(client, monkeypatch):
    reset = _scopes(monkeypatch, schedules, lambda: [[(SID,)]])
    resp = await client.delete(f"/api/v1/schedules/{SID}")
    reset()
    raw = await schedules.delete_schedule(schedule_id=SID, auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)


@pytest.mark.asyncio
async def test_schedule_fire_now(client, monkeypatch):
    make = lambda: [[{"id": SID, "enabled": True}], [(SID,)]]  # noqa: E731
    reset = _scopes(monkeypatch, schedules, make)
    resp = await client.post(f"/api/v1/schedules/{SID}/fire-now")
    reset()
    raw = await schedules.fire_schedule_now(schedule_id=SID, auth=AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == jsonable_encoder(raw)


# --------------------------------------------------------------------------- #
# The legacy /tasks/* 410 stubs are gone
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/tasks/active"),
        ("get", "/api/v1/tasks/stats"),
        ("get", "/api/v1/tasks/abc"),
        ("get", "/api/v1/tasks/abc/events"),
        ("post", "/api/v1/tasks/abc/cancel"),
    ],
)
async def test_legacy_tasks_routes_are_gone(client, method, path):
    resp = await getattr(client, method)(path)
    assert resp.status_code == 404, resp.text
