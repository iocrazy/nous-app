"""GET /api/v1/workflows/runs must read task_tracking, not dbos.workflow_status.

CLAUDE.md 路线 C rule 1: task_tracking is the UI source of truth. Earlier
the list endpoint short-circuited to DBOS.list_workflows_async, which
risked the dual-source divergence that motivated 路线 C in the first
place. These tests pin the new behavior in two ways:

  * Static: the route function body must not call DBOS.list_workflows_async
    and must reference task_tracking.
  * Behavioral: hitting the endpoint with a stubbed ORM read_scope session
    returns serialized rows in the expected shape (workflow_id renamed
    from dbos_workflow_id, etc.).
"""

from __future__ import annotations

import importlib
import inspect
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth

workflows_module = importlib.import_module("app.api.workflows_router")


# ── helpers ─────────────────────────────────────────────────────────────


def _make_auth(user_id: str | None = None) -> AuthContext:
    return AuthContext(
        user_id=user_id or str(uuid4()),
        auth_type="jwt",
        scopes=None,
        api_key_id=None,
    )


def _read_scope_returning(rows, captured):
    """A ``read_scope()`` stand-in whose session returns ``rows`` from
    ``result.mappings().all()`` and records each executed statement in
    ``captured`` so tests can assert on the compiled WHERE bindings — the
    session boundary the endpoint now runs its ORM SELECT against."""

    class _Mappings:
        def all(self):
            return rows

    class _Result:
        def mappings(self):
            return _Mappings()

    class _Session:
        async def execute(self, stmt):
            captured.append(stmt)
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


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
    the authenticated user via the ORM read_scope session."""
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
    captured: list = []

    auth = _make_auth(user_id=user_id)
    app = FastAPI()
    app.dependency_overrides[get_auth] = lambda: auth
    app.include_router(workflows_module.router, prefix="/api/v1")

    import app.db.session as db_session_mod

    with patch.object(
        db_session_mod, "read_scope", _read_scope_returning([fake_row], captured)
    ):
        client = TestClient(app)
        resp = client.get("/api/v1/workflows/runs")

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
    # The SELECT filtered by the authenticated user_id.
    params = captured[0].compile().params
    assert user_id in params.values()


@pytest.mark.unit
def test_list_workflows_maps_legacy_dbos_status_filter() -> None:
    """Legacy callers passing workflow_status=SUCCESS (DBOS terminology)
    should still work — the endpoint translates to task_tracking's
    lowercase status set so external clients aren't broken."""
    captured: list = []

    app = FastAPI()
    app.dependency_overrides[get_auth] = lambda: _make_auth()
    app.include_router(workflows_module.router, prefix="/api/v1")

    import app.db.session as db_session_mod

    with patch.object(
        db_session_mod, "read_scope", _read_scope_returning([], captured)
    ):
        client = TestClient(app)
        resp = client.get("/api/v1/workflows/runs?workflow_status=SUCCESS")

    assert resp.status_code == 200, resp.text
    # SUCCESS (DBOS terminology) maps to task_tracking's lowercase 'completed'.
    params = captured[0].compile().params
    assert "completed" in params.values()
