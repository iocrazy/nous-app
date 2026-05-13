"""GET /api/v1/workflows must read task_tracking, not dbos.workflow_status.

CLAUDE.md 路线 C rule 1: task_tracking is the UI source of truth. Earlier
the list endpoint short-circuited to DBOS.list_workflows_async, which
risked the dual-source divergence that motivated 路线 C in the first
place. These tests pin the new behavior in two ways:

  * Static: the route function body must not call DBOS.list_workflows_async
    and must reference task_tracking.
  * Behavioral: hitting the endpoint with a stubbed Supabase chain
    returns serialized rows in the expected shape (workflow_id renamed
    from dbos_workflow_id, etc.).
"""

from __future__ import annotations

import importlib
import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth

workflows_module = importlib.import_module("app.api.workflows_router")


# ── helpers (mirrored from test_a6_routers; deliberately duplicated to
# keep this test file self-contained — moving the helpers to a conftest
# is a separate cleanup, not in scope) ──────────────────────────────────


def _make_auth(user_id: str | None = None) -> AuthContext:
    return AuthContext(
        user_id=user_id or str(uuid4()),
        auth_type="jwt",
        scopes=None,
        api_key_id=None,
    )


def _app_with(router) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_auth] = lambda: _make_auth()
    app.include_router(router, prefix="/api/v1")
    return app


def _supabase_chain(execute_data: Any) -> MagicMock:
    chain = MagicMock()
    for op in ("select", "eq", "in_", "order", "limit", "range"):
        getattr(chain, op).return_value = chain
    res = MagicMock()
    res.data = execute_data
    chain.execute = AsyncMock(return_value=res)
    return chain


def _supabase_client_for(tables: dict[str, MagicMock]) -> MagicMock:
    client = MagicMock()
    client.table.side_effect = lambda name: tables.get(name, _supabase_chain([]))
    return client


# ── tests ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_workflows_reads_task_tracking_not_dbos_workflow_status() -> None:
    """Static check: list_workflows must NOT call DBOS.list_workflows_async
    and MUST reference task_tracking. This is the codified version of
    CLAUDE.md 路线 C rule 1.
    """
    source = inspect.getsource(workflows_module.list_workflows)

    assert "DBOS.list_workflows_async" not in source, (
        "list_workflows must not call DBOS.list_workflows_async — it "
        "would bypass task_tracking and re-introduce the dual-source "
        "inconsistency CLAUDE.md 路线 C rule 1 forbids."
    )
    assert "task_tracking" in source, (
        "list_workflows must read from task_tracking (the UI source of "
        "truth per CLAUDE.md 路线 C rule 1)."
    )


@pytest.mark.unit
def test_list_workflows_returns_serialized_rows() -> None:
    """Behavioral check: the endpoint returns rows with the public
    field shape (workflow_id, not dbos_workflow_id) and filters by
    the authenticated user via the supabase chain."""
    user_id = str(uuid.uuid4())
    wf_id = str(uuid.uuid4())
    fake_row = {
        "dbos_workflow_id": wf_id,
        "task_type": "download",
        "task_kind": "workflow",
        "status": "completed",
        "phase": "completed",
        "title": "Download My Video",
        "subtitle": "done",
        "progress": 100,
        "error_msg": None,
        "created_at": "2026-05-13T00:00:00Z",
        "started_at": "2026-05-13T00:00:01Z",
        "completed_at": "2026-05-13T00:00:05Z",
        "updated_at": "2026-05-13T00:00:05Z",
        "media_id": "bilibili_BV1abc",
        "resource_id": None,
        "group_id": None,
    }
    chain = _supabase_chain([fake_row])
    sb = _supabase_client_for({"task_tracking": chain})

    async def _sb():
        return sb

    auth = _make_auth(user_id=user_id)
    app = FastAPI()
    app.dependency_overrides[get_auth] = lambda: auth
    app.include_router(workflows_module.router, prefix="/api/v1")

    with patch("app.db.get_async_supabase_admin", _sb):
        client = TestClient(app)
        resp = client.get("/api/v1/workflows")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["workflows"]) == 1
    wf = body["workflows"][0]
    # The dbos_workflow_id FK is renamed to workflow_id on the wire.
    assert wf["workflow_id"] == wf_id
    assert "dbos_workflow_id" not in wf
    assert wf["task_type"] == "download"
    assert wf["status"] == "completed"
    assert wf["phase"] == "completed"
    # Supabase chain filtered by user_id.
    chain.eq.assert_any_call("user_id", user_id)


@pytest.mark.unit
def test_list_workflows_maps_legacy_dbos_status_filter() -> None:
    """Legacy callers passing workflow_status=SUCCESS (DBOS terminology)
    should still work — the endpoint translates to task_tracking's
    lowercase status set so external clients aren't broken."""
    chain = _supabase_chain([])
    sb = _supabase_client_for({"task_tracking": chain})

    async def _sb():
        return sb

    app = FastAPI()
    app.dependency_overrides[get_auth] = lambda: _make_auth()
    app.include_router(workflows_module.router, prefix="/api/v1")

    with patch("app.db.get_async_supabase_admin", _sb):
        client = TestClient(app)
        resp = client.get("/api/v1/workflows?workflow_status=SUCCESS")

    assert resp.status_code == 200, resp.text
    chain.eq.assert_any_call("status", "completed")
