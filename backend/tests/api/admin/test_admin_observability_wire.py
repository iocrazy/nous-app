"""Admin observability routes (P8 group C): wire parity after they gained
response models, the admin gate on every one of them, and the failure paths
that used to answer as a success.

Routes: ``/admin/storage/*`` (6), ``/admin/boundary-audit*`` (2),
``/admin/agent-metrics`` + ``/prometheus``. The three ``/admin/stats`` routes
this group also covered (users/growth, videos/stats, storage) had no caller
and were deleted; a test below pins that they stay gone.

Each route runs over real HTTP through the real router and the real
``get_admin_auth``; only the database session is scripted (one queue shared
by the role lookup and the route's reads, popped in order). The "before"
body of every storage route is the handler's own ``_fetch_*`` dict builder
run over the same scripted rows, so the comparison is against exactly what
FastAPI sent before the models existed. Ids come from the ORM mappers
(``sample_row``), so they are real Snowflake BIGINTs above 2**53.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import BoundaryAudit, ParsedMedia, ResourceItems, Resources
from tests.api.wire_parity import (
    SAMPLE_TS,
    assert_wire_unchanged,
    sample_orm,
    sample_row,
)

pytestmark = pytest.mark.unit

sr = importlib.import_module("app.api.admin.storage_router")
tr = importlib.import_module("app.api.admin.agent_telemetry_router")

USER = "00000000-0000-0000-0000-000000000042"
MEDIA_ID = sample_row(ParsedMedia)["id"]
RESOURCE_ID = sample_row(Resources)["id"]
SCOPE_ID = sample_row(ResourceItems)["scope_id"]

BASE = "/api/v1/admin"
TYPED_ROUTES = [
    ("GET", f"{BASE}/storage/stats"),
    ("GET", f"{BASE}/storage/media-status?media_ids={MEDIA_ID}"),
    ("GET", f"{BASE}/storage/media/{MEDIA_ID}/detail"),
    ("POST", f"{BASE}/storage/media/{MEDIA_ID}/verify"),
    ("POST", f"{BASE}/storage/verify"),
    ("GET", f"{BASE}/storage/audit"),
    ("GET", f"{BASE}/boundary-audit"),
    ("GET", f"{BASE}/boundary-audit/summary"),
    ("GET", f"{BASE}/agent-metrics"),
    ("GET", f"{BASE}/agent-metrics/prometheus"),
]


# ── scripted session ─────────────────────────────────────────────────────


class _Result:
    """Answers every read shape these routes use. ``value`` is the single
    row / scalar; ``rows`` the multi-row answer."""

    def __init__(self, value: Any = None, rows: List[Any] | None = None):
        self._value = value
        self._rows = rows if rows is not None else []

    def first(self):
        return self._value

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._value

    def mappings(self):
        return self

    def scalars(self):
        return self


class _Db:
    results: List[Any] = []


class _Session:
    async def execute(self, stmt, *a, **kw):
        item = _Db.results.pop(0) if _Db.results else _Result()
        if isinstance(item, Exception):
            raise item
        return item


@asynccontextmanager
async def _scope():
    yield _Session()


class _NoopWrite:
    async def execute(self, *a, **kw):
        return _Result()

    async def commit(self):
        return None


@asynccontextmanager
async def _write_scope():
    yield _NoopWrite()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Db.results = []
    monkeypatch.setattr("app.db.session.read_scope", _scope)
    monkeypatch.setattr("app.db.session.write_scope", _write_scope)
    monkeypatch.setattr(sr, "read_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _as_admin(*then: Any) -> None:
    _Db.results = [_Result(("admin",)), *then]


def _script(*then: Any) -> None:
    """Queue for calling a ``_fetch_*`` builder directly (no role lookup)."""
    _Db.results = list(then)


# ── admin gate ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", TYPED_ROUTES)
async def test_every_route_refuses_an_anonymous_caller(client, method, path):
    app.dependency_overrides.pop(get_auth, None)
    resp = await client.request(method, path)
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", TYPED_ROUTES)
async def test_every_route_refuses_a_non_admin(client, method, path):
    _Db.results = [_Result(("user",))]
    resp = await client.request(method, path)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/stats/users/growth",
        f"{BASE}/stats/videos/stats",
        f"{BASE}/stats/storage",
    ],
)
async def test_deleted_stats_routes_stay_gone(client, path):
    resp = await client.get(path)
    assert resp.status_code == 404, resp.text


# ── /admin/storage ───────────────────────────────────────────────────────


def _completed_audit_row() -> dict:
    return {
        "phase": "completed",
        "metadata": {
            "kind": "storage_audit",
            "scanned": 1200,
            "errors": 2,
            "missing": [
                {
                    "key": "videos/a.mp4",
                    "kind": "video",
                    "media_id": str(MEDIA_ID),
                    "resource_id": str(RESOURCE_ID),
                },
                {
                    "key": "covers/b.jpg",
                    "kind": "cover",
                    "media_id": None,
                    "resource_id": None,
                },
            ],
            "missing_truncated": False,
            "scanned_at": SAMPLE_TS.isoformat(),
        },
        "completed_at": SAMPLE_TS,
    }


def _stats_results(audit_row: dict | None) -> list:
    return [
        _Result({"count": 321, "size_bytes": 9_876_543_210}),
        _Result({"n": 4}),
        _Result({"n": 7}),
        _Result({"n": 55}),
        _Result(audit_row),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_row", [None, _completed_audit_row()])
async def test_storage_stats_wire_unchanged(client, audit_row):
    _script(*_stats_results(audit_row))
    raw = await sr._fetch_stats()

    _as_admin(*_stats_results(audit_row))
    resp = await client.get(f"{BASE}/storage/stats")
    assert_wire_unchanged(resp, raw)
    if audit_row is None:
        assert resp.json()["last_scan"] is None and resp.json()["broken"] is None
    else:
        assert resp.json()["last_scan"]["at"] == SAMPLE_TS.isoformat()


def _media_status_rows() -> list[dict]:
    base = {
        "media_id": MEDIA_ID,
        "video_key": "sb://library/videos/a.mp4",
        "video_size": 123_456_789,
        "video_download_status": "completed",
        "cover_path": "sb://library/covers/a.jpg",
        "thumbnail_path": "sb://library/thumbs/a.jpg",
        "res_cover_image": None,
        "res_file_path": "sb://library/videos/a.mp4",
        "hls_path": "sb://library/hls/a/index.m3u8",
        "rv_file_path": None,
        "scope_id": SCOPE_ID,
    }
    return [
        base,
        # fs residue, no resource / scope
        {
            **base,
            "media_id": MEDIA_ID + 1,
            "cover_path": "/data/covers/b.jpg",
            "thumbnail_path": None,
            "hls_path": None,
            "scope_id": None,
        },
        # no video
        {
            **base,
            "media_id": MEDIA_ID + 2,
            "video_key": None,
            "video_size": None,
            "video_download_status": "failed",
        },
    ]


@pytest.mark.asyncio
async def test_media_status_wire_unchanged(client):
    ids = [MEDIA_ID, MEDIA_ID + 1, MEDIA_ID + 2]
    _script(_Result(rows=_media_status_rows()))
    raw = {"rows": await sr._fetch_media_status(ids)}

    _as_admin(_Result(rows=_media_status_rows()))
    resp = await client.get(
        f"{BASE}/storage/media-status", params={"media_ids": ",".join(map(str, ids))}
    )
    assert_wire_unchanged(resp, raw)
    assert [r["storage_status"] for r in resp.json()["rows"]] == [
        "ok",
        "fs_residue",
        "no_video",
    ]


def _detail_row(*, resource: bool = True) -> dict:
    return {
        "media_id": MEDIA_ID,
        "download_path": "sb://library/videos/a.mp4",
        "storage_size": 123_456_789,
        "cover_download_path": "/data/covers/a.jpg",
        "resource_id": RESOURCE_ID if resource else None,
        "thumbnail_path": "sb://library/thumbs/a.jpg" if resource else None,
        "hls_path": None,
        "scope_id": SCOPE_ID if resource else None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", [True, False])
async def test_media_detail_wire_unchanged(client, resource):
    _script(_Result(_detail_row(resource=resource)))
    raw = await sr._fetch_media_detail(MEDIA_ID)

    _as_admin(_Result(_detail_row(resource=resource)))
    resp = await client.get(f"{BASE}/storage/media/{MEDIA_ID}/detail")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_media_detail_missing_media_is_404(client):
    _as_admin(_Result(None))
    resp = await client.get(f"{BASE}/storage/media/{MEDIA_ID}/detail")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", str(2**63), "0"])
async def test_media_id_out_of_range_is_422_not_500(client, bad):
    _as_admin()
    for method, path in [
        ("GET", f"{BASE}/storage/media/{bad}/detail"),
        ("POST", f"{BASE}/storage/media/{bad}/verify"),
    ]:
        _as_admin()
        resp = await client.request(method, path)
        assert resp.status_code == 422, (path, resp.text)


@pytest.mark.asyncio
async def test_verify_media_wire_unchanged(client, monkeypatch):
    outcomes = {
        "videos/a.mp4": "present",
        "library/derived": "missing",
        "thumbs/a.jpg": "error",
    }

    async def _probe(store, key):
        for prefix, outcome in outcomes.items():
            if key.startswith(prefix):
                return outcome
        return "missing"

    monkeypatch.setattr("app.workflows.storage_audit._probe_one", _probe)
    monkeypatch.setattr(
        "app.services.library.media_storage.library_store", lambda: object()
    )

    _script(_Result(_detail_row()))
    detail = await sr._fetch_media_detail(MEDIA_ID)
    raw = {"results": await sr._verify_keys(object(), detail["assets"])}
    assert {r["exists"] for r in raw["results"]} == {True, False, None}

    _as_admin(_Result(_detail_row()))
    resp = await client.post(f"{BASE}/storage/media/{MEDIA_ID}/verify")
    assert_wire_unchanged(resp, raw)


class _FakeManager:
    def __init__(self, *, create_error: Exception | None = None):
        self.created: list[dict] = []
        self.failed: list[tuple] = []
        self._create_error = create_error

    async def create(self, **kw):
        if self._create_error:
            raise self._create_error
        self.created.append(kw)

    async def fail(self, task_id, error_msg, **kw):
        self.failed.append((task_id, error_msg, kw))


@pytest.fixture
def dispatch(monkeypatch):
    box: dict[str, Any] = {
        "manager": _FakeManager(),
        "dispatched": [],
        "error": None,
        "audit_logs": [],
    }

    async def _start(task_type, *, workflow_id, **kw):
        if box["error"]:
            raise box["error"]
        box["dispatched"].append(workflow_id)
        return {"mode": "dbos", "task_type": task_type, "dbos_workflow_id": workflow_id}

    async def _audit_log(**kw):
        box["audit_logs"].append(kw)

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _start
    )
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: box["manager"],
    )
    monkeypatch.setattr(sr, "create_audit_log", _audit_log)
    return box


@pytest.mark.asyncio
async def test_deep_verify_dispatch_wire_unchanged(client, dispatch):
    _as_admin(_Result(None))  # no running audit
    resp = await client.post(f"{BASE}/storage/verify")
    wf = dispatch["dispatched"][0]
    assert_wire_unchanged(resp, {"workflow_id": wf, "already_running": False})
    assert dispatch["manager"].created[0]["dbos_workflow_id"] == wf
    assert dispatch["audit_logs"][0]["target_id"] == wf


@pytest.mark.asyncio
async def test_deep_verify_dedups_a_running_audit(client, dispatch):
    _as_admin(_Result({"dbos_workflow_id": "wf-running"}))
    resp = await client.post(f"{BASE}/storage/verify")
    assert_wire_unchanged(resp, {"workflow_id": "wf-running", "already_running": True})
    assert dispatch["dispatched"] == []


@pytest.mark.asyncio
async def test_deep_verify_dispatch_failure_fails_the_row_and_says_so(client, dispatch):
    """Before P8 a dispatch error left the pre-created row 'queued' forever:
    the retry answered {already_running: true} for 2 hours and the Storage
    page polled a scan that never started."""
    dispatch["error"] = RuntimeError("DBOS orchestrator is not enabled")
    _as_admin(_Result(None))
    resp = await client.post(f"{BASE}/storage/verify")
    assert resp.status_code == 503, resp.text
    assert resp.json()["details"]["code"] == "storage_audit_dispatch_failed"
    created = dispatch["manager"].created[0]["dbos_workflow_id"]
    assert [f[0] for f in dispatch["manager"].failed] == [created]
    assert dispatch["audit_logs"] == []


@pytest.mark.asyncio
async def test_deep_verify_row_create_failure_is_typed_and_never_dispatches(
    client, monkeypatch, dispatch
):
    dispatch["manager"] = _FakeManager(create_error=RuntimeError("fk violation"))
    _as_admin(_Result(None))
    resp = await client.post(f"{BASE}/storage/verify")
    assert resp.status_code == 503, resp.text
    assert resp.json()["details"]["code"] == "storage_audit_dispatch_failed"
    assert dispatch["dispatched"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        None,
        _completed_audit_row(),
        {"phase": "processing", "metadata": {}, "completed_at": None},
        # metadata stored as a JSON string by an older writer
        {"phase": "failed", "metadata": '{"scanned": 3}', "completed_at": None},
    ],
)
async def test_storage_audit_wire_unchanged(client, row):
    _script(_Result(row))
    raw = await sr._fetch_latest_audit()

    _as_admin(_Result(row))
    resp = await client.get(f"{BASE}/storage/audit")
    assert_wire_unchanged(resp, raw)
    # the never-scanned answer has no missing_truncated key, as before
    assert ("missing_truncated" in resp.json()) is (row is not None)


@pytest.mark.asyncio
async def test_storage_audit_tolerates_a_malformed_missing_list(client):
    row = {"phase": "completed", "metadata": {"missing": "oops"}}
    _as_admin(_Result(row))
    resp = await client.get(f"{BASE}/storage/audit")
    assert resp.status_code == 200, resp.text
    assert resp.json()["missing"] == []


# ── /admin/boundary-audit ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_boundary_audit_list_wire_unchanged(client):
    rows = [
        sample_orm(BoundaryAudit),
        sample_orm(
            BoundaryAudit,
            id=sample_row(BoundaryAudit)["id"] + 1,
            raw_url=None,
            resolved_ip=None,
            user_id=None,
            request_id=None,
            metadata_json=None,
        ),
    ]
    _as_admin(_Result(42), _Result(rows=rows))
    resp = await client.get(f"{BASE}/boundary-audit", params={"limit": 2, "offset": 3})

    boundary = importlib.import_module("app.api.admin.boundary_audit_router")
    raw = {
        "items": [boundary._serialize(r) for r in rows],
        "total": 42,
        "limit": 2,
        "offset": 3,
    }
    assert_wire_unchanged(resp, raw)
    assert resp.json()["items"][0]["blocked_at"].endswith("+00:00")
    assert resp.json()["items"][0]["id"] > 2**53


@pytest.mark.asyncio
async def test_boundary_audit_summary_wire_unchanged(client):
    rows = [
        ("l1_validate", "private_ip"),
        ("l1_validate", "private_ip"),
        ("l3_proxy", "connect_blocked"),
    ]
    _as_admin(_Result(rows=rows))
    resp = await client.get(f"{BASE}/boundary-audit/summary")
    assert_wire_unchanged(
        resp,
        {
            "by_layer": {"l1_validate": 2, "l3_proxy": 1},
            "by_reason": {"private_ip": 2, "connect_blocked": 1},
            "total_7d": 3,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,then",
    [
        (f"{BASE}/boundary-audit", 1),
        (f"{BASE}/boundary-audit/summary", 0),
    ],
)
async def test_boundary_audit_read_failure_is_503_not_an_empty_list(client, path, then):
    """A DB error used to come back 200 with an empty list: the same body as a
    week with no attacks."""
    _as_admin(*[_Result(0)] * then, RuntimeError("relation does not exist"))
    resp = await client.get(path)
    assert resp.status_code == 503, resp.text
    assert resp.json()["details"]["code"] == "boundary_audit_unavailable"


# ── /admin/agent-metrics ─────────────────────────────────────────────────

_STATE_KEYS = (
    "agent_metrics",
    "bounds_registry",
    "root_abort_registry",
    "model_health",
    "hook_registry",
    "context_engines",
)


@pytest.fixture
def harness_state(monkeypatch):
    from app.agent_framework import bounds as bounds_mod
    from app.agent_framework import model_health as health_mod
    from app.agent_framework.abort_controller import AbortController
    from app.agent_framework.bounds import BoundsAdvertisement, BoundsRegistry
    from app.agent_framework.model_health import ModelHealthRegistry
    from app.agent_framework.root_abort_registry import RootAbortRegistry
    from app.agent_framework.telemetry import AgentMetrics

    clock = SimpleNamespace(time=lambda: 1_758_000_000.25)
    monkeypatch.setattr(bounds_mod, "time", clock)
    monkeypatch.setattr(health_mod, "time", clock)

    metrics = AgentMetrics()
    metrics.inc("compaction_triggered", by=3)
    metrics.inc("typo_counter", by=2)

    bounds = BoundsRegistry()
    bounds.register(
        BoundsAdvertisement(
            worker_id="worker-1",
            role="worker",
            workflows=frozenset({"storage_audit", "transcribe"}),
            agents=frozenset({"script_ai"}),
            providers=frozenset({"qwen"}),
            lane_capacity={"default": 4},
            version=None,
            started_at=1_757_999_000.5,
        )
    )

    roots = RootAbortRegistry()
    roots.register_root("run-root", AbortController())
    roots.register_child(parent_run_id="run-root", child_run_id="run-child")

    health = ModelHealthRegistry()
    health.mark_cooled_down("qwen-max", seconds=30, reason="429")

    values = {
        "agent_metrics": metrics,
        "bounds_registry": bounds,
        "root_abort_registry": roots,
        "model_health": health,
        "hook_registry": SimpleNamespace(names=lambda: ["memory_harvester"]),
        "context_engines": SimpleNamespace(names=lambda: ["chat"]),
    }
    saved = {k: getattr(app.state, k, None) for k in _STATE_KEYS}
    had = {k: hasattr(app.state, k) for k in _STATE_KEYS}
    for k, v in values.items():
        setattr(app.state, k, v)
    yield values
    for k in _STATE_KEYS:
        if had[k]:
            setattr(app.state, k, saved[k])
        elif hasattr(app.state, k):
            delattr(app.state, k)


@pytest.mark.asyncio
async def test_agent_metrics_wire_unchanged(client, harness_state):
    raw = await tr.agent_metrics(SimpleNamespace(app=app), None)
    assert raw["counters"]["_unknown"] == {"typo_counter": 2}
    assert raw["bounds"]["worker-1"]["version"] is None

    _as_admin()
    resp = await client.get(f"{BASE}/agent-metrics")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_agent_metrics_wire_unchanged_before_startup(client):
    saved = {k: getattr(app.state, k) for k in _STATE_KEYS if hasattr(app.state, k)}
    for k in saved:
        delattr(app.state, k)
    try:
        _as_admin()
        resp = await client.get(f"{BASE}/agent-metrics")
        assert_wire_unchanged(
            resp,
            {
                "counters": {},
                "bounds": {},
                "root_aborts": {},
                "model_health": {},
                "hooks_registered": [],
                "context_engines": [],
            },
        )
    finally:
        for k, v in saved.items():
            setattr(app.state, k, v)


@pytest.mark.asyncio
async def test_prometheus_is_plain_text(client, harness_state):
    from app.agent_framework.prometheus_exporter import render_prometheus

    _as_admin()
    resp = await client.get(f"{BASE}/agent-metrics/prometheus")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert resp.text == render_prometheus(harness_state["agent_metrics"])


def test_openapi_declares_prometheus_as_text_not_json():
    op = app.openapi()["paths"][f"{BASE}/agent-metrics/prometheus"]["get"]
    content = op["responses"]["200"]["content"]
    assert list(content) == ["text/plain"]
    assert content["text/plain"]["schema"] == {"type": "string"}
