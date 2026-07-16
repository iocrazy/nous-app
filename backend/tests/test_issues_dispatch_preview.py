"""GET /issues/{id}/dispatch-preview — the server-side dispatch predicate.

The confirm dialog must report what the server would actually do, so the
preview mirrors dispatch_issue's guards. These pin each branch of that
predicate; the endpoint is called directly (as in test_issues_dispatch_client)
since the HTTP path needs heavy auth/DB setup.
"""

from __future__ import annotations

import asyncio
import importlib

from app.schemas.issue import DispatchBlockedReason
from app.services.infra import dbos_orchestrator

# `app/api/__init__.py` rebinds `issues_router` to the APIRouter instance, so
# load the actual module rather than the shadowed name.
issues_router = importlib.import_module("app.api.issues_router")

_AUTH = object()


def _setup(monkeypatch, row, *, dbos_enabled: bool = True) -> None:
    async def _fake_get(issue_id):  # noqa: ANN001, ANN202
        return row

    monkeypatch.setattr(issues_router.issue_repository, "get_by_id", _fake_get)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: dbos_enabled)
    # Visibility is asserted elsewhere; keep these focused on the predicate.
    monkeypatch.setattr(issues_router, "_assert_visibility", lambda row, auth: None)


def _preview(issue_id: int = 1):
    return asyncio.run(issues_router.dispatch_preview(issue_id, _AUTH))


def test_will_start_when_assigned_and_dispatchable(monkeypatch):
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "11111111-1111-1111-1111-111111111111",
            "status": "todo",
            "dbos_workflow_id": None,
        },
    )
    r = _preview()
    assert r.will_start is True
    assert r.agent_id == "11111111-1111-1111-1111-111111111111"
    assert r.blocked_reason is None


def test_blocked_without_assignee(monkeypatch):
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": None,
            "status": "todo",
            "dbos_workflow_id": None,
        },
    )
    r = _preview()
    assert r.will_start is False
    assert r.blocked_reason is DispatchBlockedReason.NO_ASSIGNEE
    assert r.agent_id is None


def test_blocked_when_dbos_disabled(monkeypatch):
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": "todo",
            "dbos_workflow_id": None,
        },
        dbos_enabled=False,
    )
    r = _preview()
    assert r.will_start is False
    assert r.blocked_reason is DispatchBlockedReason.DBOS_DISABLED


def test_blocked_on_terminal_status(monkeypatch):
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": "done",
            "dbos_workflow_id": None,
        },
    )
    r = _preview()
    assert r.will_start is False
    assert r.blocked_reason is DispatchBlockedReason.TERMINAL_STATUS


def test_blocked_when_already_running(monkeypatch):
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": "in_progress",
            "dbos_workflow_id": "issue-1-abc123",
        },
    )
    r = _preview()
    assert r.will_start is False
    assert r.blocked_reason is DispatchBlockedReason.ALREADY_RUNNING


def test_redispatch_allowed_after_a_finished_run(monkeypatch):
    """A stale workflow_id from a prior run must not block a re-dispatch —
    only a live (in_progress) run does."""
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": "todo",
            "dbos_workflow_id": "issue-1-oldrun",
        },
    )
    r = _preview()
    assert r.will_start is True
    assert r.blocked_reason is None
