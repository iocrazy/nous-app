"""Probe endpoints: wire parity after they gained response models (P9).

``/health``, ``/api/version``, ``/dbos/health``, ``/dbos/routing`` and
``/health/deep`` now declare ``response_model``; each body must equal what
FastAPI sent for the handler's dict with no model (``wire_parity.py``).

``/api/v1/readyz`` is the deploy smoke's only trusted probe, so it is
declared but NOT enforced (``response_model=None`` + ``responses=``): these
tests pin that the route still has no response model, that every verdict's
real payload validates against ``ReadyzResponse`` (``extra="forbid"``, so a
new key in the handler without a declaration fails here), and that the
status code and body are exactly what the handler computed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext
from app.main import app
from app.schemas.probes import ReadyzResponse
from tests.api.wire_parity import assert_wire_unchanged

health_mod = sys.modules["app.api.health_router"]
lifespan_mod = sys.modules["app.api.lifespan_router"]
dbos_orch = sys.modules["app.services.infra.dbos_orchestrator"]

pytestmark = pytest.mark.unit


async def _fake_admin() -> AuthContext:
    return AuthContext(user_id="00000000-0000-0000-0000-000000000001", auth_type="jwt")


@pytest.fixture(autouse=True)
def _admin():
    app.dependency_overrides[get_admin_auth] = _fake_admin
    yield
    app.dependency_overrides.pop(get_admin_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── /health, /api/version ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root_health_wire(client) -> None:
    resp = await client.get("/health")
    assert_wire_unchanged(resp, {"status": "healthy", "message": "Service is running"})


def _build_info(monkeypatch, content: str | None) -> None:
    real_exists, real_read = Path.exists, Path.read_text

    def _exists(self):
        if str(self) == "/app/build-info.json":
            return content is not None
        return real_exists(self)

    def _read(self, *a: Any, **k: Any):
        if str(self) == "/app/build-info.json":
            return content
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "exists", _exists)
    monkeypatch.setattr(Path, "read_text", _read)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "raw"),
    [
        (None, {"commit_sha": None, "available": False}),
        ("{not json", {"commit_sha": None, "available": False}),
        (
            json.dumps(
                {
                    "service": "backend",
                    "version": "1.2.3",
                    "commit_sha": "abc1234",
                    "commit_count": 5,
                    "commits": [{"sha": "abc1234"}],
                }
            ),
            {
                "commit_sha": "abc1234",
                "commit_count": 5,
                "service": "backend",
                "version": "1.2.3",
                "available": True,
            },
        ),
        # A minimal file: missing keys come back as null / defaults.
        (
            "{}",
            {
                "commit_sha": None,
                "commit_count": None,
                "service": "backend",
                "version": "latest",
                "available": True,
            },
        ),
    ],
    ids=["missing", "malformed", "full", "empty"],
)
async def test_api_version_wire(client, monkeypatch, content, raw) -> None:
    _build_info(monkeypatch, content)
    resp = await client.get("/api/version")
    assert_wire_unchanged(resp, raw)
    # No key the handler did not send (exclude_unset).
    assert set(resp.json()) == set(raw)


# ── /dbos/* ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_dbos_health_wire(client, monkeypatch, enabled) -> None:
    monkeypatch.setattr(dbos_orch, "is_enabled", lambda: enabled)
    resp = await client.get("/api/v1/dbos/health")
    assert_wire_unchanged(resp, {"enabled": enabled})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cache", "loaded_at"),
    [({}, 0.0), ({"transcribe": "dbos", "a_parse": "celery"}, 1234.5678)],
    ids=["never-loaded", "loaded"],
)
async def test_dbos_routing_wire(client, monkeypatch, cache, loaded_at) -> None:
    async def _refresh() -> None:
        dbos_orch._routing_cache = dict(cache)
        dbos_orch._routing_loaded_at = loaded_at

    monkeypatch.setattr(dbos_orch, "_routing_cache", {})
    monkeypatch.setattr(dbos_orch, "_routing_loaded_at", 0.0)
    monkeypatch.setattr(dbos_orch, "_refresh_routing_cache", _refresh)
    resp = await client.get("/api/v1/dbos/routing")
    raw = {"loaded_at": loaded_at, "task_types": sorted(cache.items())}
    assert_wire_unchanged(resp, raw)


# ── /health/deep ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["bare", "populated"])
async def test_health_deep_wire(client, monkeypatch, variant) -> None:
    async def _ok() -> str:
        return "reachable"

    async def _down() -> str:
        raise ConnectionError("boom")

    monkeypatch.setattr(health_mod, "_probe_supabase", _ok)
    monkeypatch.setattr(health_mod, "_probe_redis", _down)
    monkeypatch.setattr(dbos_orch, "is_enabled", lambda: True)

    state = app.state
    names = [
        "process_role",
        "bounds_registry",
        "hook_registry",
        "context_engines",
        "model_health",
        "lane_queue",
        "agent_metrics",
        "prometheus_pusher",
        "root_abort_registry",
    ]
    for name in names:
        monkeypatch.delattr(state, name, raising=False)

    if variant == "populated":

        class _Bounds:
            def live_bounds(self):
                return [1, 2]

        class _Engines:
            def names(self):
                return ["chat", "script"]

        class _Health:
            def snapshot(self):
                return {
                    "m1": {
                        "health": "ok",
                        "reason": None,
                        "cooldown_remaining_s": 0.0,
                    }
                }

        class _LaneKey:
            value = "main"

        class _Queue:
            def qsize(self):
                return 3

        class _Lane:
            _queues = {_LaneKey(): _Queue()}

        class _Metrics:
            @property
            def counters(self):
                raise RuntimeError("metrics gone")

        class _Aborts:
            def __len__(self):
                return 4

        monkeypatch.setattr(
            state, "process_role", SimpleNamespace(value="api"), raising=False
        )
        monkeypatch.setattr(state, "bounds_registry", _Bounds(), raising=False)
        monkeypatch.setattr(state, "hook_registry", ["h1", "h2"], raising=False)
        monkeypatch.setattr(state, "context_engines", _Engines(), raising=False)
        monkeypatch.setattr(state, "model_health", _Health(), raising=False)
        monkeypatch.setattr(state, "lane_queue", _Lane(), raising=False)
        monkeypatch.setattr(state, "agent_metrics", _Metrics(), raising=False)
        monkeypatch.setattr(state, "prometheus_pusher", object(), raising=False)
        monkeypatch.setattr(state, "root_abort_registry", _Aborts(), raising=False)

    # Build the dict the handler builds, with the models out of the way.
    route = next(
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == "/api/v1/health/deep"
    )
    request = SimpleNamespace(app=app)
    raw = await route.endpoint(request, None)

    resp = await client.get("/api/v1/health/deep")
    body = resp.json()
    # ``ms`` is wall-clock; everything else must match byte for byte.
    for probe in body["probes"].values():
        probe["ms"] = 0
    for probe in raw["probes"].values():
        probe["ms"] = 0
    assert resp.status_code == 200, resp.text
    assert body == jsonable_encoder(raw)
    assert body["probes"]["redis"]["status"] == "down"
    if variant == "populated":
        assert body["in_process"]["agent_metrics_keys_in_use"] == {
            "error": "RuntimeError: metrics gone"
        }


# ── /readyz (declared, not enforced) ─────────────────────────────────────


def test_readyz_is_declared_not_enforced() -> None:
    route = next(
        r for r in app.routes if isinstance(r, APIRoute) and r.path == "/api/v1/readyz"
    )
    assert route.response_model is None
    op = app.openapi()["paths"]["/api/v1/readyz"]["get"]
    for code in ("200", "503"):
        schema = op["responses"][code]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ReadyzResponse"}


class _FakeRegistry:
    def __init__(self, done: bool, dead: bool = False):
        self._done = done
        self._dead = dead

    def status_snapshot(self):
        return [
            {
                "name": "seed_loader",
                "done": self._done,
                "long_running": False,
                "gates_readiness": True,
                "fatal": False,
                "duration_seconds": 1.25 if self._done else None,
                "error": None,
            },
            {
                "name": "reap_sweep",
                "done": self._dead,
                "long_running": True,
                "gates_readiness": True,
                "fatal": False,
                "duration_seconds": None,
                "error": "RuntimeError: died" if self._dead else None,
            },
        ]

    def all_done(self) -> bool:
        return self._done and not self._dead

    def dead_daemons(self):
        return ["reap_sweep"] if self._dead else []

    def failed_fatal_gates(self):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registry", "dsn", "launched", "sample", "verdict", "code"),
    [
        (_FakeRegistry(True), "", False, None, "ready", 200),
        (_FakeRegistry(False), "", False, None, "starting", 503),
        (_FakeRegistry(True, dead=True), "", False, None, "degraded", 503),
        (_FakeRegistry(True), "postgres://x", False, None, "degraded", 503),
        (
            _FakeRegistry(True),
            "postgres://x",
            True,
            {
                "status": "ok",
                "used": 12,
                "max_connections": 100,
                "percent": 12.0,
                "sampled_at": 1727140923.5,
            },
            "ready",
            200,
        ),
    ],
    ids=["ready", "starting", "degraded-daemon", "dbos-broken", "dbos-ok-sampled"],
)
async def test_readyz_payload_conforms(
    client, monkeypatch, registry, dsn, launched, sample, verdict, code
) -> None:
    monkeypatch.setenv("DBOS_DATABASE_URL", dsn)
    monkeypatch.setattr(dbos_orch, "is_launched", lambda: launched)

    async def _cached():
        return sample

    import app.services.infra.pg_connection_monitor as mon

    monkeypatch.setattr(mon, "get_cached_sample", _cached)
    monkeypatch.setattr(app.state, "bg_tasks", registry, raising=False)

    expected = await lifespan_mod._readyz_payload(registry, Response())
    resp = await client.get("/api/v1/readyz")
    assert resp.status_code == code, resp.text
    assert resp.json() == expected  # untouched by any model
    assert resp.json()["status"] == verdict
    ReadyzResponse.model_validate(resp.json())


@pytest.mark.asyncio
async def test_readyz_not_ready_conforms(client, monkeypatch) -> None:
    monkeypatch.delattr(app.state, "bg_tasks", raising=False)
    resp = await client.get("/api/v1/readyz")
    assert resp.status_code == 503, resp.text
    assert resp.json() == {
        "status": "not_ready",
        "reason": "background task registry missing on app.state",
        "tasks": [],
    }
    ReadyzResponse.model_validate(resp.json())
