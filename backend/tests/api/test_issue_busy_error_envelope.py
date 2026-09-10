"""The ``issue_busy`` refusals as the CLIENT actually receives them.

Both new refusals (fork, resume) raise ``HTTPException(409, detail={code,
message})``, but nothing reaches a browser in that shape: the global handler
wraps every error in ``ErrorResponse``, which puts the typed payload under
``details`` and sets top-level ``code`` to ``http_409``. Asserting the bare
``detail`` dict in a unit test would pass while every production refusal
degraded to an untyped ``http_409`` — so these go through a real app with the
handlers registered, exercising the endpoints' own mapping code.
"""

from __future__ import annotations

import datetime as dt
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=UUID(ME))


def _marker() -> dict:
    return {
        "dispatching": {
            "workflow_id": "issue-7-abc",
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    }


def _app(route: str, handler) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.post(route)(handler)
    return TestClient(app, raise_server_exceptions=False)


def test_fork_issue_busy_reaches_the_client_typed(monkeypatch):
    from app.services.issues import issue_fork

    deps = SimpleNamespace(
        get_run=AsyncMock(
            return_value={"id": 42, "issue_id": 9, "conversation_id": 100}
        ),
        get_issue=AsyncMock(
            return_value={
                "id": 9,
                "status": "in_progress",
                "ai_session_id": 100,
                "hidden_at": None,
                "execution_locked_at": None,
                "execution_state": _marker(),
            }
        ),
        running_root_run_id=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(issue_fork, "default_deps", lambda: deps)

    importlib.import_module("app.api.ai_library_router")
    lib = sys.modules["app.api.ai_library_router"]

    async def _route() -> dict:
        return await lib.fork_run_endpoint("42", lib.ForkRunRequest(at_seq=2), AUTH)

    resp = _app("/fork", _route).post("/fork")

    assert resp.status_code == 409
    body = resp.json()
    assert body["details"] == {
        "code": "issue_busy",
        "message": "a dispatch is in flight",
    }
    assert body["code"] == "http_409" and body["success"] is False


def test_resume_issue_busy_reaches_the_client_typed(monkeypatch):
    importlib.import_module("app.api.issues_router")
    r = sys.modules["app.api.issues_router"]

    issue = {
        "id": 7,
        "status": "in_progress",
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "ai_session_id": 55,
        "paused_at": dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc),
        "execution_locked_at": None,
        "dbos_workflow_id": None,
        "execution_state": _marker(),
    }
    monkeypatch.setattr(r, "is_issue_visible", AsyncMock(return_value=True))
    monkeypatch.setattr(
        r,
        "issue_repository",
        SimpleNamespace(
            get_by_id=AsyncMock(return_value=issue), set_paused_at=AsyncMock()
        ),
    )
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: SimpleNamespace(running_root_run_id=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: SimpleNamespace(pending_count=AsyncMock(return_value=1)),
    )

    async def _route() -> dict:
        return await r.resume_issue(7, AUTH)

    resp = _app("/resume", _route).post("/resume")

    assert resp.status_code == 409
    body = resp.json()
    assert body["details"] == {
        "code": "issue_busy",
        "message": "a dispatch is in flight",
    }
    assert body["code"] == "http_409" and body["success"] is False
