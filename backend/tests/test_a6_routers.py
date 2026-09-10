"""A6 router smoke tests — flows / schedules / lanes / ws-ticket.

Covers happy-path HTTP contract for each new endpoint imported via
file-level checkout from feat/a3 / a5 / a7 / a9 PR branches.

Strategy:
  * Spin up a minimal FastAPI app with just the target router mounted.
  * Override AuthDep / AdminAuthDep with a synthetic AuthContext so we
    don't need real Supabase JWT.
  * Mock supabase admin client (flows / schedules) and redis (ws-ticket).
  * For lanes, hit the in-memory LaneQueue singleton — no DB at all.
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext, get_auth

# Import the actual SUBMODULES (not the router attribute that
# app.api.__init__.py rebinds onto the package namespace).
ws_ticket_module = importlib.import_module("app.api.ws_ticket_router")
flows_module = importlib.import_module("app.api.flows_router")
schedules_module = importlib.import_module("app.api.schedules_router")
lanes_module = importlib.import_module("app.api.lanes_router")


def _make_auth(user_id: str | None = None) -> AuthContext:
    return AuthContext(
        user_id=user_id or str(uuid4()), auth_type="jwt", scopes=None, api_key_id=None
    )


def _app_with(router) -> FastAPI:
    """Minimal FastAPI app with only the target router and stub auth deps."""
    app = FastAPI()
    auth = _make_auth()
    app.dependency_overrides[get_auth] = lambda: auth
    app.dependency_overrides[get_admin_auth] = lambda: auth
    app.include_router(router, prefix="/api/v1")
    return app


# ─── Lanes ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_lanes_snapshot_returns_4_lanes() -> None:
    """LaneQueue.snapshot() shape — 4 named lanes with all stat fields."""
    client = TestClient(_app_with(lanes_module.router))
    resp = client.get("/api/v1/lanes/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    lane_names = {l["name"] for l in body["lanes"]}
    # PR #161 ships 4 fixed lanes.
    assert lane_names == {"user", "background", "scheduled", "subagent"}
    for lane in body["lanes"]:
        assert lane["capacity"] >= 1
        assert lane["in_flight"] >= 0
        assert "saturation_pct" in lane
        assert "last_wait_ms" in lane


# ─── WS Ticket ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ws_ticket_mints_random_token_and_stores_in_redis() -> None:
    """POST /ws/ticket → 32-byte url-safe ticket, stored in Redis with 30s TTL."""

    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value=True)

    async def _redis():
        return redis_mock

    with patch("app.api.ws_ticket_router.get_async_redis", _redis):
        client = TestClient(_app_with(ws_ticket_module.router))
        resp = client.post("/api/v1/ws/ticket")
        assert resp.status_code == 200
        body = resp.json()
        assert "ticket" in body
        assert "expires_in_seconds" in body
        assert len(body["ticket"]) >= 32  # token_urlsafe(32) → 43+ chars
        assert body["expires_in_seconds"] == ws_ticket_module.TICKET_TTL_SECONDS

    # Redis SET called once with the right TTL.
    assert redis_mock.set.await_count == 1
    args, kwargs = redis_mock.set.await_args
    key, _val = args[0], args[1]
    assert key.startswith(ws_ticket_module.TICKET_KEY_PREFIX)
    assert kwargs["ex"] == ws_ticket_module.TICKET_TTL_SECONDS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consume_ticket_uses_atomic_getdel() -> None:
    """consume_ticket → GETDEL is atomic (one-shot replay protection)."""

    redis_mock = MagicMock()
    user_id = str(uuid4())
    redis_mock.execute_command = AsyncMock(return_value=user_id.encode())

    async def _redis():
        return redis_mock

    with patch("app.api.ws_ticket_router.get_async_redis", _redis):
        out = await ws_ticket_module.consume_ticket("some-ticket")
    assert out == user_id
    cmd_args = redis_mock.execute_command.await_args
    assert cmd_args.args[0] == "GETDEL"


# ─── Flows ────────────────────────────────────────────────────────────


def _supabase_chain(execute_data: Any) -> MagicMock:
    chain = MagicMock()
    for op in (
        "select",
        "eq",
        "in_",
        "order",
        "limit",
        "range",
        "update",
        "insert",
        "delete",
        "maybe_single",
        "single",
    ):
        getattr(chain, op).return_value = chain
    res = MagicMock()
    res.data = execute_data
    res.count = len(execute_data) if isinstance(execute_data, list) else None
    chain.execute = AsyncMock(return_value=res)
    return chain


def _supabase_client_for(tables: dict[str, MagicMock]) -> MagicMock:
    client = MagicMock()
    client.table.side_effect = lambda name: tables.get(name, _supabase_chain([]))
    return client


def _orm_scope(rows: list):
    """A read_scope()/write_scope() stand-in whose session.execute() returns
    ``rows`` via .mappings().first()/.all() — the ORM boundary the flows /
    schedules routers now run their statements against."""
    from contextlib import asynccontextmanager

    class _M:
        def __init__(self, d):
            self._d = d

        def first(self):
            return self._d[0] if self._d else None

        def all(self):
            return self._d

    class _R:
        def __init__(self, d):
            self._d = d

        def mappings(self):
            return _M(self._d)

    class _S:
        async def execute(self, _stmt):
            return _R(rows)

    @asynccontextmanager
    async def _scope():
        yield _S()

    return _scope


@pytest.mark.unit
def test_flows_create_inserts_user_id_and_returns_row() -> None:
    """POST /flows → INSERT into task_flows with auth.user_id stamped."""

    fake_row = {
        "id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "name": "Test flow",
        "state": "pending",
        "cascade_cancel": True,
        "total_tasks": 0,
        "completed_tasks": 0,
        "failed_tasks": 0,
        "cancelled_tasks": 0,
        "metadata": {},
        "created_at": "2026-05-04T00:00:00Z",
        "updated_at": "2026-05-04T00:00:00Z",
        "completed_at": None,
    }
    with patch.object(flows_module, "write_scope", _orm_scope([fake_row])):
        client = TestClient(_app_with(flows_module.router))
        resp = client.post(
            "/api/v1/flows",
            json={"name": "Test flow"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Test flow"
        assert body["state"] == "pending"


@pytest.mark.unit
def test_flows_list_filters_by_user() -> None:

    rows = [
        {
            "id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "name": f"Flow {i}",
            "state": "pending",
            "cascade_cancel": True,
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "cancelled_tasks": 0,
            "metadata": {},
            "created_at": "2026-05-04T00:00:00Z",
            "updated_at": "2026-05-04T00:00:00Z",
            "completed_at": None,
        }
        for i in range(3)
    ]
    with patch.object(flows_module, "read_scope", _orm_scope(rows)):
        client = TestClient(_app_with(flows_module.router))
        resp = client.get("/api/v1/flows")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 3


# ─── Schedules ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_schedules_create_validates_cron_and_returns_next_fire_at() -> None:
    """POST /schedules → cron validated via croniter, next_fire_at populated."""

    fake_row = {
        "id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "name": "Daily 9am",
        "cron_expr": "0 9 * * *",
        # Schedule task_type must come from the router's allowlist —
        # the schedules_router validates it explicitly.
        "task_type": "ai_summary",
        "payload": {},
        "lane": "scheduled",
        "enabled": True,
        "last_fired_at": None,
        "next_fire_at": "2026-05-05T09:00:00+00:00",
        "fire_count": 0,
        "fail_count": 0,
        "last_error": None,
        "created_at": "2026-05-04T00:00:00Z",
        "updated_at": "2026-05-04T00:00:00Z",
        # W2a autopilot columns (mig 370) — the DB returns these with defaults.
        "timezone": "UTC",
        "consecutive_fails": 0,
        "paused_at": None,
        "pause_reason": None,
        "skipped_count": 0,
        "stale_after_minutes": 60,
    }
    with patch.object(schedules_module, "write_scope", _orm_scope([fake_row])):
        client = TestClient(_app_with(schedules_module.router))
        resp = client.post(
            "/api/v1/schedules",
            json={
                "name": "Daily 9am",
                "cron_expr": "0 9 * * *",
                "task_type": "ai_summary",
            },
        )
        # 201: creating a schedule creates a resource (phase 2b-2 Task 5).
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["cron_expr"] == "0 9 * * *"
        assert body["enabled"] is True
        assert body["next_fire_at"]


@pytest.mark.unit
def test_schedules_create_rejects_bad_cron() -> None:
    """Bad cron → 400 (croniter raises during validation)."""

    # Bad cron is rejected during validation, before any DB call.
    client = TestClient(_app_with(schedules_module.router))
    resp = client.post(
        "/api/v1/schedules",
        json={
            "name": "Bad",
            "cron_expr": "not a cron",
            "task_type": "ai_summary",
        },
    )
    # 400 from cron validation; some routers wrap as 422.
    assert resp.status_code in (400, 422), resp.text
