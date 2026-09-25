"""Admin operations routes (OpenAPI P8, group D2): wire parity after they
gained response models, the admin gate on every one, and the typed 404s.

Routes: ``/admin/transcode/{id}/retry`` + ``/batch``, ``/admin/tasks/{id}/
cancel``, ``/admin/jimeng/status`` + ``/login`` + ``/logout``,
``/admin/celery/workers`` + ``/queues``, ``/ai-library/admin/reload-seeds`` +
``/telemetry``.

Every case runs over real HTTP through the real router and the real
``get_admin_auth``. The role lookup (and, for telemetry, the ``agent_runs``
read) goes through a scripted ``read_scope`` session; repositories, the DBOS
dispatch, the audit log and the ``dreamina`` subprocess are faked at the
router's seams. Each parity test builds the dict the handler used to return
and asserts the response equals ``jsonable_encoder`` of it.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from fastapi.encoders import jsonable_encoder
from httpx import ASGITransport, AsyncClient

import app.api.admin  # noqa: F401 — package __init__ rebinds the module names
from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import AgentRuns, ResourceVersions
from tests.api.wire_parity import assert_wire_unchanged, sample_row

pytestmark = pytest.mark.unit

ADMIN = "00000000-0000-0000-0000-000000000042"

transcode_mod = sys.modules["app.api.admin.transcode_router"]
tasks_mod = sys.modules["app.api.admin.tasks_router"]
jimeng_mod = sys.modules["app.api.admin.jimeng_auth_router"]
celery_mod = sys.modules["app.api.admin.celery_router"]
ai_library_mod = sys.modules["app.api.ai_library_router"]

VERSION = sample_row(ResourceVersions, only=["id", "resource_id"])
VERSION_ID = VERSION["id"]  # a real Snowflake BIGINT above 2**53
TASK_ID = "5f0c2a9e-1111-4222-8333-944445555666"

ROUTES = [
    ("POST", f"/api/v1/admin/transcode/{VERSION_ID}/retry"),
    ("POST", "/api/v1/admin/transcode/batch?action=retry_failed"),
    ("POST", f"/api/v1/admin/tasks/{TASK_ID}/cancel"),
    ("GET", "/api/v1/admin/jimeng/status"),
    ("POST", "/api/v1/admin/jimeng/login"),
    ("POST", "/api/v1/admin/jimeng/logout"),
    ("GET", "/api/v1/admin/celery/workers"),
    ("GET", "/api/v1/admin/celery/queues"),
    ("POST", "/api/v1/ai-library/admin/reload-seeds"),
    ("GET", "/api/v1/ai-library/admin/telemetry"),
]


# ── scripted read_scope: role lookup first, then any queued reads ─────────


class _Result:
    def __init__(self, first: Any = None, rows: list[dict] | None = None):
        self._first = first
        self._rows = rows or []

    def first(self) -> Any:
        return self._first

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


class _Db:
    role: str | None = "admin"
    reads: list[_Result] = []
    statements: list[str] = []


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=ADMIN, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Db.role = "admin"
    _Db.reads = []
    _Db.statements = []

    class _Session:
        def __init__(self) -> None:
            self.role_served = False

        async def execute(self, stmt, *a, **kw):
            sql = str(stmt)
            _Db.statements.append(sql)
            if "user_profiles" in sql:
                return _Result((_Db.role,) if _Db.role is not None else None)
            return _Db.reads.pop(0) if _Db.reads else _Result()

        def add(self, *a, **kw) -> None:  # request-log middleware writes
            return None

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    monkeypatch.setattr("app.db.session.write_scope", _scope)

    audits: list[dict] = []

    async def _audit(**kw):
        audits.append(kw)

    for mod in (transcode_mod, tasks_mod):
        monkeypatch.setattr(mod, "create_audit_log", _audit)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# ── admin gate ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ROUTES)
async def test_every_route_refuses_a_non_admin(client, method, path):
    _Db.role = "user"
    resp = await client.request(method, path)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ROUTES)
async def test_every_route_refuses_an_anonymous_caller(client, method, path):
    app.dependency_overrides.pop(get_auth, None)
    resp = await client.request(method, path)
    assert resp.status_code == 401, resp.text


# ── /admin/transcode ──────────────────────────────────────────────────────


class _TranscodeRepo:
    def __init__(self, version: dict | None, *, matched: bool = True):
        self.version = version
        self.matched = matched
        self.batch: list[dict] = []
        self.marked: list[str] = []

    async def get_version(self, version_id: str):
        return self.version

    async def mark_pending(self, version_id: str) -> bool:
        self.marked.append(version_id)
        return self.matched

    async def list_versions_for_batch(self, action: str):
        return self.batch


@pytest.fixture
def dispatched(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _start(kind, **kw):
        calls.append(kw["dbos_workflow_kwargs"])

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _start
    )
    return calls


def _use_transcode_repo(monkeypatch, repo: _TranscodeRepo) -> None:
    monkeypatch.setattr(transcode_mod, "get_admin_transcode_repository", lambda: repo)


@pytest.mark.asyncio
async def test_transcode_retry_wire(client, monkeypatch, dispatched):
    version = {**VERSION, "mime_type": "video/mp4"}
    _use_transcode_repo(monkeypatch, _TranscodeRepo(version))
    resp = await client.post(f"/api/v1/admin/transcode/{VERSION_ID}/retry")
    raw = {"message": "Transcode retry queued", "version_id": str(VERSION_ID)}
    assert_wire_unchanged(resp, raw)
    assert dispatched == [
        {
            "resource_id": str(VERSION["resource_id"]),
            "version_id": str(VERSION_ID),
            "user_id": ADMIN,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "12x", "99999999999999999999", "-1"])
async def test_transcode_retry_non_numeric_id_is_404_not_500(
    client, monkeypatch, dispatched, bad
):
    """A path id that cannot be a BIGINT used to reach the int8 bind and 500."""
    repo = _TranscodeRepo({**VERSION, "mime_type": "video/mp4"})

    async def _unreachable(version_id):
        raise AssertionError("an impossible id must not reach the query")

    repo.get_version = _unreachable
    _use_transcode_repo(monkeypatch, repo)
    resp = await client.post(f"/api/v1/admin/transcode/{bad}/retry")
    assert resp.status_code == 404, resp.text
    assert dispatched == []


@pytest.mark.asyncio
async def test_transcode_retry_vanished_version_is_typed_404(
    client, monkeypatch, dispatched
):
    """Deleted between the read and the write: 404, and nothing dispatched."""
    version = {**VERSION, "mime_type": "video/mp4"}
    _use_transcode_repo(monkeypatch, _TranscodeRepo(version, matched=False))
    resp = await client.post(f"/api/v1/admin/transcode/{VERSION_ID}/retry")
    _assert_typed_404(resp)
    assert dispatched == []


@pytest.mark.asyncio
@pytest.mark.parametrize("has_more", [False, True])
async def test_transcode_batch_wire(client, monkeypatch, dispatched, has_more):
    repo = _TranscodeRepo(None)
    count = transcode_mod.BATCH_VERSIONS_LIMIT if has_more else 2
    repo.batch = [
        {"id": VERSION_ID + i, "resource_id": VERSION["resource_id"]}
        for i in range(count)
    ]
    _use_transcode_repo(monkeypatch, repo)
    resp = await client.post("/api/v1/admin/transcode/batch?action=retry_failed")
    raw = {
        "message": f"Batch retry_failed: {count} transcode tasks queued",
        "total_found": count,
        "queued": count,
        "has_more": has_more,
    }
    assert_wire_unchanged(resp, raw)
    assert len(dispatched) == count


@pytest.mark.asyncio
async def test_transcode_batch_skips_a_vanished_version(
    client, monkeypatch, dispatched
):
    repo = _TranscodeRepo(None, matched=False)
    repo.batch = [{"id": VERSION_ID, "resource_id": VERSION["resource_id"]}]
    _use_transcode_repo(monkeypatch, repo)
    resp = await client.post("/api/v1/admin/transcode/batch?action=transcode_new")
    assert resp.status_code == 200, resp.text
    assert resp.json()["queued"] == 0
    assert dispatched == []


# ── /admin/tasks ──────────────────────────────────────────────────────────


class _TasksRepo:
    def __init__(self, task: dict | None):
        self.task = task

    async def get(self, task_id: str):
        return self.task


def _task(status: str) -> dict:
    return {"dbos_workflow_id": TASK_ID, "status": status, "task_type": "download"}


@pytest.fixture
def dbos_cancel(monkeypatch) -> list[str]:
    """The DBOS-native cancel the Task Center uses; records workflow ids."""
    import app.services.infra.dbos_orchestrator as orch

    workflows_mod = sys.modules["app.api.workflows_router"]
    cancelled: list[str] = []

    async def _cancel(workflow_id: str) -> None:
        cancelled.append(workflow_id)

    monkeypatch.setattr(orch, "is_enabled", lambda: True)
    monkeypatch.setattr(workflows_mod, "_cancel", _cancel)
    return cancelled


def _task_tracking_writes() -> list[str]:
    return [
        s
        for s in _Db.statements
        if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        and "task_tracking" in s
    ]


@pytest.mark.asyncio
async def test_task_cancel_wire_goes_through_dbos(client, monkeypatch, dbos_cancel):
    """Cancel asks DBOS and leaves ``task_tracking`` to the lifecycle trigger.
    It used to PATCH ``status/phase='cancelled'`` and never touch the
    workflow, which kept running."""
    monkeypatch.setattr(
        tasks_mod, "get_admin_tasks_repository", lambda: _TasksRepo(_task("processing"))
    )
    resp = await client.post(f"/api/v1/admin/tasks/{TASK_ID}/cancel")
    assert_wire_unchanged(resp, {"message": "Cancel requested", "task_id": TASK_ID})
    assert dbos_cancel == [TASK_ID]
    assert _task_tracking_writes() == []


@pytest.mark.asyncio
async def test_task_cancel_refuses_a_terminal_task(client, monkeypatch, dbos_cancel):
    monkeypatch.setattr(
        tasks_mod, "get_admin_tasks_repository", lambda: _TasksRepo(_task("failed"))
    )
    resp = await client.post(f"/api/v1/admin/tasks/{TASK_ID}/cancel")
    assert resp.status_code == 400, resp.text
    assert dbos_cancel == []


@pytest.mark.asyncio
async def test_task_cancel_dbos_refusal_is_not_a_success(
    client, monkeypatch, dbos_cancel
):
    workflows_mod = sys.modules["app.api.workflows_router"]

    async def _boom(workflow_id: str) -> None:
        raise RuntimeError("no such workflow")

    monkeypatch.setattr(workflows_mod, "_cancel", _boom)
    monkeypatch.setattr(
        tasks_mod, "get_admin_tasks_repository", lambda: _TasksRepo(_task("pending"))
    )
    resp = await client.post(f"/api/v1/admin/tasks/{TASK_ID}/cancel")
    assert resp.status_code == 400, resp.text
    assert _task_tracking_writes() == []


@pytest.mark.asyncio
async def test_task_cancel_without_dbos_is_503(client, monkeypatch, dbos_cancel):
    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "is_enabled", lambda: False)
    monkeypatch.setattr(
        tasks_mod, "get_admin_tasks_repository", lambda: _TasksRepo(_task("pending"))
    )
    resp = await client.post(f"/api/v1/admin/tasks/{TASK_ID}/cancel")
    assert resp.status_code == 503, resp.text
    assert dbos_cancel == []


@pytest.mark.asyncio
async def test_admin_task_retry_route_is_gone(client):
    """It reset the row to pending/queued and dispatched nothing, so the task
    sat in pending for ever. Retry lives in the owner's Task Center."""
    resp = await client.post(f"/api/v1/admin/tasks/{TASK_ID}/retry")
    assert resp.status_code in (404, 405), resp.text


# ── /admin/jimeng ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "health,raw",
    [
        ({"ok": True, "credit": 4200}, {"logged_in": True, "credit": 4200}),
        ({"ok": True}, {"logged_in": True}),
        (
            {"ok": False, "error": "not_logged_in"},
            {"logged_in": False, "error": "not_logged_in"},
        ),
        ({"ok": False}, {"logged_in": False}),
    ],
)
async def test_jimeng_status_wire(client, monkeypatch, health, raw):
    async def _health(self):
        return health

    monkeypatch.setattr(jimeng_mod.JimengCliProvider, "health", _health)
    resp = await client.get("/api/v1/admin/jimeng/status")
    assert_wire_unchanged(resp, raw)


class _Proc:
    returncode: int | None = None
    stdout = None

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        self.returncode = -9


@pytest.fixture
def login_proc(monkeypatch):
    async def _exec(*cmd, **kw):
        return _Proc()

    monkeypatch.setattr(jimeng_mod.asyncio, "create_subprocess_exec", _exec)
    yield
    session = jimeng_mod._current_login
    jimeng_mod._current_login = None
    if session is not None:
        for task in (session._kill_task, session._drain_task):
            if task is not None:
                task.cancel()


@pytest.mark.asyncio
async def test_jimeng_login_pending_wire(client, monkeypatch, login_proc):
    material = {
        "verification_uri": "https://jimeng.jianying.com/ai-tool/cli-auth?u=abc",
        "user_code": "a8b16ef3",
        "device_code": "b9fc128ece579cb87f1f9c09a49e3633",
        "expires_at": "2099-01-01T00:00:00Z",
    }

    async def _material(proc, timeout):
        return dict(material)

    monkeypatch.setattr(jimeng_mod, "_read_login_material", _material)
    resp = await client.post("/api/v1/admin/jimeng/login")
    public = {k: v for k, v in material.items() if k != "device_code"}
    assert_wire_unchanged(resp, {"status": "pending", **public})
    assert "b9fc128ece579cb87f1f9c09a49e3633" not in resp.text


@pytest.mark.asyncio
async def test_jimeng_login_already_wire(client, monkeypatch, login_proc):
    async def _material(proc, timeout):
        raise jimeng_mod._LoginExited(0)

    monkeypatch.setattr(jimeng_mod, "_read_login_material", _material)
    resp = await client.post("/api/v1/admin/jimeng/login")
    assert_wire_unchanged(resp, {"status": "already"})


@pytest.mark.asyncio
async def test_jimeng_logout_wire(client, monkeypatch):
    async def _run(args, timeout):
        return 0, "", ""

    monkeypatch.setattr(jimeng_mod, "_run_cli_once", _run)
    resp = await client.post("/api/v1/admin/jimeng/logout")
    assert_wire_unchanged(resp, {"logged_in": False})


# ── /admin/celery ─────────────────────────────────────────────────────────


@pytest.fixture
def dbos(monkeypatch):
    from app.services.infra import dbos_orchestrator

    state = {"enabled": True, "running": 3, "enqueued": 5}
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: state["enabled"])

    def _list(**kw):
        n = state["enqueued"] if kw.get("status") == "ENQUEUED" else state["running"]
        return [object()] * n

    monkeypatch.setattr(celery_mod, "_list_workflows", _list)
    return state


@pytest.mark.asyncio
async def test_celery_workers_wire(client, dbos):
    resp = await client.get("/api/v1/admin/celery/workers")
    raw = {
        "online": 1,
        "total": 1,
        "workers": [
            {
                "name": "dbos@local",
                "status": "online",
                "active": 3,
                "processed": None,
                "concurrency": 8,
                "uptime": None,
            }
        ],
    }
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_celery_workers_dbos_off_wire(client, dbos):
    dbos["enabled"] = False
    resp = await client.get("/api/v1/admin/celery/workers")
    assert_wire_unchanged(resp, {"online": 0, "total": 0, "workers": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_celery_queues_wire(client, dbos, enabled):
    dbos["enabled"] = enabled
    resp = await client.get("/api/v1/admin/celery/queues")
    queues = [{"name": "agent_workforce", "messages": 5}] if enabled else []
    assert_wire_unchanged(resp, {"queues": queues})


# ── /ai-library/admin ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_seeds_wire(client, monkeypatch):
    results = {
        "agents": 4,
        "skills": 9,
        "agent_skill_bindings": 12,
        "skipped": {"agents": 1, "skills": 2, "skill_files": 3},
        "upserted": {"agents": 3, "skills": 7, "skill_files": 11},
        "errors": [
            {
                "scope": "skill",
                "slug": "script-outline",
                "error": {
                    "type": "APIError",
                    "message": "boom",
                    "code": "23505",
                    "details": {"k": "v"},
                    "status": 409,
                },
            }
        ],
    }

    class _Loader:
        def __init__(self, **kw) -> None:
            pass

        async def load_all(self):
            return results

    monkeypatch.setattr(ai_library_mod, "SeedLoader", _Loader)
    monkeypatch.setattr(ai_library_mod, "_repos", lambda: (None, None))
    resp = await client.post("/api/v1/ai-library/admin/reload-seeds")
    assert_wire_unchanged(resp, results)


def _run_row(**overrides: Any) -> dict:
    cols = [
        "agent_id",
        "user_id",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "own_cost_cents",
        "started_at",
        "error_code",
    ]
    row = sample_row(AgentRuns, only=cols)
    row["cost_cents"] = row.pop("own_cost_cents")
    row.update(overrides)
    return row


def _telemetry_rows() -> list[dict]:
    agent_a, agent_b = uuid.UUID(int=11), uuid.UUID(int=12)
    user_a = uuid.UUID(int=21)
    day2 = dt.datetime(2026, 9, 23, 5, 0, tzinfo=dt.timezone.utc)
    return [
        _run_row(agent_id=agent_a, user_id=user_a, status="completed"),
        _run_row(agent_id=agent_a, user_id=user_a, status="failed", error_code=None),
        _run_row(
            agent_id=agent_b,
            user_id=user_a,
            status="failed",
            error_code="timeout",
            started_at=day2,
            cost_cents=None,
        ),
    ]


@pytest.mark.asyncio
async def test_telemetry_wire(client):
    """The handler's own output (called directly, no response model) against
    the HTTP body. ``window_start`` / ``window_end`` come from ``now()`` so
    they are compared by shape, everything else byte for byte."""
    _Db.reads = [_Result(rows=_telemetry_rows())]
    resp = await client.get("/api/v1/ai-library/admin/telemetry?days=7")

    _Db.reads = [_Result(rows=_telemetry_rows())]
    raw = await ai_library_mod.admin_telemetry(
        AuthContext(user_id=ADMIN, auth_type="jwt"), days=7
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected = jsonable_encoder(raw)
    for key in ("window_start", "window_end"):
        assert body[key].endswith("+00:00"), body[key]
        dt.datetime.fromisoformat(body.pop(key))
        expected.pop(key)
    assert body == expected
    # The fixture exercised every section, not an empty rollup.
    assert body["overview"]["total_runs"] == 3
    assert {m["error_code"] for m in body["failure_modes"]} == {"unknown", "timeout"}
    assert len(body["daily_trend"]) == 2


@pytest.mark.asyncio
async def test_telemetry_empty_window_wire(client):
    _Db.reads = [_Result(rows=[])]
    resp = await client.get("/api/v1/ai-library/admin/telemetry")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overview"] == {
        "total_runs": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cost_cents": 0,
        "unique_agents": 0,
        "unique_users": 0,
    }
    assert body["top_agents"] == body["daily_trend"] == body["failure_modes"] == []


@pytest.mark.asyncio
async def test_admin_lane_snapshot_is_gone(client):
    """It read ``LaneQueue._queues``, an attribute the queue never had (it is
    ``_lanes``), so it always answered ``{"available": true, "lanes": {}}``.
    Nothing called it; ``GET /api/v1/lanes/snapshot`` is the lane view."""
    resp = await client.get("/api/v1/ai-library/admin/lanes/snapshot")
    assert resp.status_code in (404, 405), resp.text
