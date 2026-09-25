"""Task-manager routes: wire parity after they gained response models (P4).

Each route is driven over real HTTP. Where the handler goes through
``UnifiedTaskManager``, the REAL manager runs against a stub session, so the
rows reach the router through ``_serialize_task_row`` exactly as in
production. Every row carries every ``task_tracking`` column with its native
type (``tests/api/wire_parity.py``), and the body must equal what FastAPI sent
for the bare dict.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import TaskTracking
from app.schemas.task_manager_responses import TaskTrackingRow
from app.services.infra import unified_task_manager as utm
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_row

tm = importlib.import_module("app.api.task_manager_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
BASE = "/api/v1/task-manager"


def native_row(**overrides: Any) -> Dict[str, Any]:
    """A ``task_tracking`` row as the DB driver returns it (native types)."""
    row = sample_row(TaskTracking)
    row["task_type"] = "download"
    row["status"] = "failed"
    row["phase"] = "failed"
    row["metadata"] = {"version_id": "v9", "retry_count": 2}
    row["subscribers"] = [
        {
            "user_id": USER,
            "resource_id": "7300000000000000001",
            "subscribed_at": "2026-09-24T01:02:03.456789+00:00",
        }
    ]
    row.update(overrides)
    return row


def nullable_row() -> Dict[str, Any]:
    """Every nullable column null."""
    nullable = {c.name for c in TaskTracking.__table__.columns if c.nullable and c.name}
    return native_row(**{name: None for name in nullable})


class _Result:
    def __init__(self, value: Any = None, *, rowcount: int = 0):
        self._value = value
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value

    def all(self):
        return self._value

    def scalars(self):
        return self

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """Hands back queued results in execute order."""

    def __init__(self) -> None:
        self.results: List[_Result] = []
        self.statements: List[Any] = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return self.results.pop(0) if self.results else _Result()


@pytest.fixture
def session(monkeypatch) -> _Session:
    s = _Session()

    @asynccontextmanager
    async def _scope(*args, **kwargs):
        yield s

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)
    return s


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


def serialized(row: Dict[str, Any]) -> Dict[str, Any]:
    return utm._serialize_task_row(row)


def test_row_model_covers_every_task_tracking_column():
    assert set(TaskTrackingRow.model_fields) == column_names(TaskTracking)


# ── lists ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [native_row(), nullable_row()], ids=["full", "nulls"])
async def test_list_tasks(client, session, row):
    session.results = [_Result(137), _Result([row])]
    resp = await client.get(f"{BASE}/tasks", params={"limit": 20, "offset": 40})
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "data": [serialized(row)],
            "total": 137,
            "page": 3,
            "page_size": 20,
        },
    )
    body = resp.json()["data"][0]
    if row["issue_id"] is not None:
        # BIGINT stays a JSON number, beyond 2**53 intact.
        assert body["issue_id"] == row["issue_id"]
        assert body["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_list_tasks_empty_page(client, session):
    session.results = [_Result(0), _Result([])]
    resp = await client.get(f"{BASE}/tasks")
    assert_wire_unchanged(
        resp, {"success": True, "data": [], "total": 0, "page": 1, "page_size": 50}
    )


@pytest.mark.asyncio
async def test_active_tasks(client, session):
    row = native_row(status="processing", phase="processing")
    session.results = [_Result([row])]
    resp = await client.get(f"{BASE}/tasks/active")
    assert_wire_unchanged(resp, {"success": True, "data": [serialized(row)]})


@pytest.mark.asyncio
async def test_task_ids(client, session):
    session.results = [_Result(["wf-1", "wf-2"])]
    resp = await client.get(f"{BASE}/tasks/ids", params={"statuses": ["failed"]})
    assert_wire_unchanged(
        resp, {"success": True, "ids": ["wf-1", "wf-2"], "total": 2, "capped": False}
    )


@pytest.mark.asyncio
async def test_task_ids_filter_excluding_every_terminal_status(client, session):
    resp = await client.get(f"{BASE}/tasks/ids", params={"statuses": ["pending"]})
    assert_wire_unchanged(
        resp, {"success": True, "ids": [], "total": 0, "capped": False}
    )


# ── counts ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_counts(client, session):
    session.results = [_Result(["download", "download", "canvas_gen", None])]
    resp = await client.get(f"{BASE}/active-counts")
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "data": {"total": 3, "by_type": {"download": 2, "canvas_gen": 1}},
        },
    )


_STATS_ROWS = [
    {"task_type": "download", "status": "processing"},
    {"task_type": "canvas_gen", "status": "lost"},
    {"task_type": "ai_summary", "status": "completed"},
]


@pytest.mark.asyncio
async def test_stats(client, session):
    session.results = [_Result(_STATS_ROWS)]
    resp = await client.get(f"{BASE}/tasks/stats")
    session.results = [_Result(_STATS_ROWS)]
    raw = await utm.get_task_manager().get_stats(USER)
    assert_wire_unchanged(resp, {"success": True, "data": raw})
    assert raw["active_total"] == 1 and raw["by_type"]["download"] == 1


# ── acks ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel(client, session):
    session.results = [_Result({"dbos_workflow_id": "wf-1", "phase": "completed"})]
    resp = await client.post(f"{BASE}/tasks/wf-1/cancel")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
async def test_delete(client, session):
    session.results = [_Result(rowcount=1)]
    resp = await client.delete(f"{BASE}/tasks/wf-1")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
async def test_clear_completed(client, session):
    session.results = [_Result(["wf-keep"]), _Result(rowcount=7)]
    resp = await client.post(f"{BASE}/tasks/clear-completed")
    assert_wire_unchanged(resp, {"success": True, "cleared": 7})


# ── health controls ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_override_returns_the_patched_row(client, session):
    row = native_row(max_duration_minutes=90, do_not_auto_cancel=True)
    session.results = [_Result(row)]
    resp = await client.patch(
        f"{BASE}/tasks/wf-1/health-override", json={"max_duration_minutes": 90}
    )
    task = {
        k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in row.items()
    }
    assert_wire_unchanged(resp, {"success": True, "task": task})
    assert resp.json()["task"]["heartbeat_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_health_override_miss_is_a_typed_404(client, session):
    session.results = [_Result(None)]
    resp = await client.patch(
        f"{BASE}/tasks/wf-gone/health-override", json={"do_not_auto_cancel": True}
    )
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_extend(client, session):
    session.results = [
        _Result({"started_at": None, "max_duration_minutes": None}),
        _Result({"dbos_workflow_id": "wf-1"}),
    ]
    resp = await client.post(f"{BASE}/tasks/wf-1/extend", params={"minutes": 45})
    assert_wire_unchanged(resp, {"success": True, "max_duration_minutes": 45})


@pytest.mark.asyncio
async def test_extend_counts_from_started_at(client, session):
    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10, seconds=5)
    session.results = [
        _Result({"started_at": started, "max_duration_minutes": 5}),
        _Result({"dbos_workflow_id": "wf-1"}),
    ]
    resp = await client.post(f"{BASE}/tasks/wf-1/extend", params={"minutes": 30})
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "max_duration_minutes": 40}


@pytest.mark.asyncio
async def test_extend_write_miss_is_a_typed_404(client, session):
    """The row went away between the read and the UPDATE (deleted, or retried
    onto a new workflow id). Answering success would claim a cap that was
    never stored."""
    session.results = [
        _Result({"started_at": None, "max_duration_minutes": None}),
        _Result(None),
    ]
    resp = await client.post(f"{BASE}/tasks/wf-1/extend")
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# ── retry ────────────────────────────────────────────────────────────


@pytest.fixture
def captured_retry(monkeypatch):
    """Run the real manager's retry, keeping what it handed the router."""
    real = utm.get_task_manager()
    seen: Dict[str, Any] = {}

    class _Tracker:
        async def retry_task(self, task_id, user_id, *, new_workflow_id=None):
            seen["task"] = await real.retry_task(
                task_id, user_id, new_workflow_id=new_workflow_id
            )
            return seen["task"]

    monkeypatch.setattr(tm, "get_task_manager", lambda: _Tracker())
    monkeypatch.setattr(tm, "_require_media_parser", AsyncMock())
    return seen


@pytest.mark.asyncio
async def test_retry_parse_without_url_returns_the_row(
    client, session, monkeypatch, captured_retry
):
    row = native_row(task_type="parse", dedup_key="legacy-no-prefix")
    monkeypatch.setattr(
        tm,
        "_peek_task_row",
        AsyncMock(
            return_value={
                "task_type": "parse",
                "dedup_key": row["dedup_key"],
                "status": "failed",
            }
        ),
    )
    session.results = [_Result(row)]
    resp = await client.post(f"{BASE}/tasks/{row['dbos_workflow_id']}/retry")
    assert_wire_unchanged(resp, {"success": True, "data": captured_retry["task"]})
    data = resp.json()["data"]
    assert data["status"] == "pending" and data["phase"] == "queued"
    assert data["error_msg"] is None and data["started_at"] is None
    assert data["dbos_workflow_id"] != row["dbos_workflow_id"]


@pytest.mark.asyncio
async def test_retry_extract_audio_dispatches_and_returns_the_row(
    client, session, monkeypatch, captured_retry
):
    row = native_row(task_type="extract_audio")
    monkeypatch.setattr(
        tm,
        "_peek_task_row",
        AsyncMock(
            return_value={
                "task_type": "extract_audio",
                "dedup_key": None,
                "status": "failed",
            }
        ),
    )
    started = AsyncMock()
    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", started)
    session.results = [_Result(row)]
    resp = await client.post(f"{BASE}/tasks/{row['dbos_workflow_id']}/retry")
    assert_wire_unchanged(resp, {"success": True, "data": captured_retry["task"]})
    assert (
        started.await_args.kwargs["workflow_id"]
        == resp.json()["data"]["dbos_workflow_id"]
    )


# ── progress ─────────────────────────────────────────────────────────


def _progress_row(**overrides: Any) -> Dict[str, Any]:
    row = native_row(status="processing")
    keep = (
        "dbos_workflow_id",
        "progress",
        "status",
        "speed",
        "total_bytes",
        "error_msg",
        "subtitle",
    )
    out = {k: row[k] for k in keep}
    out.update(overrides)
    return out


@pytest.fixture
def no_redis(monkeypatch):
    import app.core.redis as redis_mod

    def _boom():
        raise RuntimeError("no redis in test env")

    monkeypatch.setattr(redis_mod, "get_sync_redis", _boom)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {
            "progress": None,
            "speed": None,
            "total_bytes": None,
            "error_msg": None,
            "subtitle": None,
        },
    ],
    ids=["full", "nulls"],
)
async def test_progress_db_fallback_has_no_downloaded_key(
    client, session, no_redis, overrides
):
    row = _progress_row(**overrides)
    session.results = [_Result(row)]
    resp = await client.get(f"{BASE}/tasks/wf-1/progress")
    assert_wire_unchanged(
        resp,
        {
            "task_id": "wf-1",
            "dbos_workflow_id": row["dbos_workflow_id"],
            "status": row["status"],
            "percent": row["progress"],
            "speed": row["speed"],
            "total": row["total_bytes"],
            "error": row["error_msg"],
            "subtitle": row["subtitle"],
        },
    )
    assert "downloaded" not in resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redis_payload",
    [
        {
            "percent": 42,
            "downloaded": 4_200_000,
            "total": 10_000_000,
            "speed": "2.5 MB/s",
            "status": "downloading",
        },
        # downloader.failed(): float percent, error present
        {
            "percent": 12.5,
            "downloaded": 1,
            "total": 8,
            "speed": "0 B/s",
            "status": "failed",
            "error": "boom",
        },
        # tasks.download_progress.failed(): only percent/status/error
        {"percent": 0, "status": "failed", "error": "boom"},
    ],
    ids=["live", "failed-float", "failed-sparse"],
)
async def test_progress_redis_branch(client, session, monkeypatch, redis_payload):
    row = _progress_row()
    session.results = [_Result(row)]

    class _Redis:
        def get(self, key):
            assert key == f"download_progress:{row['dbos_workflow_id']}"
            return json.dumps(redis_payload)

    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "get_sync_redis", lambda: _Redis())
    resp = await client.get(f"{BASE}/tasks/wf-1/progress")
    data = redis_payload
    assert_wire_unchanged(
        resp,
        {
            "task_id": "wf-1",
            "dbos_workflow_id": row["dbos_workflow_id"],
            "status": data.get("status", "downloading"),
            "percent": data.get("percent", 0),
            "downloaded": data.get("downloaded", 0),
            "total": data.get("total", 0),
            "speed": data.get("speed", "0 B/s"),
            "error": data.get("error"),
            "subtitle": row["subtitle"],
        },
    )


@pytest.mark.asyncio
async def test_progress_unknown_task_is_404(client, session, no_redis):
    session.results = [_Result(None)]
    resp = await client.get(f"{BASE}/tasks/nope/progress")
    assert resp.status_code == 404
