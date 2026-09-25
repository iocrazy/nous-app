"""/api/v1/workflows/{id}/… only serves the workflow's owner.

DBOS has no notion of users, so before this guard any signed-in caller with
an id could read another user's workflow input/output (``/status``,
``/steps``, ``/events``), cancel it, resume it, or fork it (``/restart``).

Two layers:

- ``caller_owns_workflow`` against a stubbed session: which records grant.
  The agent-run case matters: the Task Center forks through ``/restart``
  exactly when the ``task_tracking`` row is gone (see
  ``frontend/services/taskRetry.ts``), so an owner with only an
  ``agent_runs`` row must still pass.
- The routes over real HTTP, paired: the owner reaches DBOS, an outsider gets
  the typed 404 and DBOS is never called.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth

access = importlib.import_module("app.api.workflow_access")
wr = importlib.import_module("app.api.workflows_router")

OWNER = str(uuid4())
OUTSIDER = str(uuid4())
WF = "parse-0123abcd-456789abcdef"


# ── caller_owns_workflow ────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Result":
        return self

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._rows


def _scope(tracked: list, runs: list, issues: list):
    """read_scope stand-in answering the three lookups in the guard's order."""
    answers = [tracked, runs, issues]

    class _Session:
        async def execute(self, _stmt):
            return _Result(answers.pop(0))

    @asynccontextmanager
    async def scope():
        yield _Session()

    return scope


async def _owns(monkeypatch, *, tracked=(), runs=(), issues=(), visible=False):
    monkeypatch.setattr(
        access, "read_scope", _scope(list(tracked), list(runs), list(issues))
    )

    async def fake_visible(row, user_id):
        return visible

    monkeypatch.setattr(access, "is_issue_visible", fake_visible)
    return await access.caller_owns_workflow(WF, OWNER)


@pytest.mark.asyncio
async def test_task_tracking_owner_is_granted(monkeypatch):
    assert await _owns(monkeypatch, tracked=[OWNER]) is True


@pytest.mark.asyncio
async def test_someone_elses_task_tracking_row_is_refused(monkeypatch):
    assert await _owns(monkeypatch, tracked=[OUTSIDER]) is False


@pytest.mark.asyncio
async def test_agent_run_owner_without_tracking_row_is_granted(monkeypatch):
    """The Task Center fork fallback: no task_tracking row, an agent run."""
    assert await _owns(monkeypatch, runs=[OWNER]) is True


@pytest.mark.asyncio
async def test_someone_elses_agent_run_is_refused(monkeypatch):
    assert await _owns(monkeypatch, runs=[OUTSIDER]) is False


@pytest.mark.asyncio
async def test_visible_issue_dispatch_is_granted(monkeypatch):
    issue = {"created_by_user_id": OUTSIDER, "assignee_user_id": None, "team_id": 7}
    assert await _owns(monkeypatch, issues=[issue], visible=True) is True


@pytest.mark.asyncio
async def test_invisible_issue_dispatch_is_refused(monkeypatch):
    issue = {"created_by_user_id": OUTSIDER, "assignee_user_id": None, "team_id": 7}
    assert await _owns(monkeypatch, issues=[issue], visible=False) is False


@pytest.mark.asyncio
async def test_unknown_id_is_refused(monkeypatch):
    assert await _owns(monkeypatch) is False


# ── routes, paired ──────────────────────────────────────────────────────


@pytest.fixture
def dbos_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(wr.dbos_orchestrator, "is_enabled", lambda: True)

    async def status_read(wf):
        calls.append("status")
        return SimpleNamespace(workflow_uuid=wf, status="SUCCESS")

    async def steps_read(wf):
        calls.append("steps")
        return []

    async def cancel(wf):
        calls.append("cancel")

    async def resume(wf):
        calls.append("resume")

    async def fork(wf):
        calls.append("fork")
        return SimpleNamespace(workflow_id="forked-1")

    monkeypatch.setattr(wr, "_status_read", status_read)
    monkeypatch.setattr(wr, "_steps_read", steps_read)
    monkeypatch.setattr(wr, "_cancel", cancel)
    monkeypatch.setattr(wr, "_resume", resume)
    monkeypatch.setattr(wr, "_fork", fork)

    async def owns(workflow_id, user_id):
        return workflow_id == WF and str(user_id) == OWNER

    monkeypatch.setattr(access, "caller_owns_workflow", owns)
    return calls


def _client(user_id: str) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_auth] = lambda: AuthContext(
        user_id=user_id, auth_type="jwt", scopes=None, api_key_id=None
    )
    app.include_router(wr.router, prefix="/api/v1")
    return TestClient(app)


ROUTES = [
    ("get", "status", 200, "status"),
    ("get", "steps", 200, "steps"),
    ("post", "cancel", 200, "cancel"),
    ("post", "resume", 200, "resume"),
    ("post", "restart", 202, "fork"),
]


@pytest.mark.parametrize("method,action,ok_code,dbos_call", ROUTES)
def test_owner_reaches_dbos(dbos_calls, method, action, ok_code, dbos_call):
    resp = getattr(_client(OWNER), method)(f"/api/v1/workflows/{WF}/{action}")
    assert resp.status_code == ok_code, resp.text
    assert dbos_call in dbos_calls


@pytest.mark.parametrize("method,action,ok_code,dbos_call", ROUTES)
def test_outsider_gets_typed_404_and_dbos_is_untouched(
    dbos_calls, method, action, ok_code, dbos_call
):
    resp = getattr(_client(OUTSIDER), method)(f"/api/v1/workflows/{WF}/{action}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "not_found_or_out_of_scope"
    assert dbos_calls == []


def _events(monkeypatch, ticket_user: str):
    ticket_mod = importlib.import_module("app.api.ws_ticket_router")

    async def consume(ticket):
        return ticket_user

    monkeypatch.setattr(ticket_mod, "consume_ticket", consume)
    return _client(ticket_user).get(f"/api/v1/workflows/{WF}/events?ticket=t")


def test_events_ticket_holder_who_owns_the_workflow_gets_the_stream(
    dbos_calls, monkeypatch
):
    resp = _events(monkeypatch, OWNER)
    assert resp.status_code == 200, resp.text
    assert "event: status" in resp.text


def test_events_ticket_holder_who_does_not_own_it_gets_404(dbos_calls, monkeypatch):
    resp = _events(monkeypatch, OUTSIDER)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "not_found_or_out_of_scope"
    assert dbos_calls == []
