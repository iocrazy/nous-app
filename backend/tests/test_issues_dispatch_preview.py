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
    # (async since D6.1 team folding made the assert query team_members.)
    async def _fake_visibility(row, auth):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(issues_router, "_assert_visibility", _fake_visibility)


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


# ── Task 2 review fold-in: terminal issues are refused, typed ─────────────

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402


@pytest.mark.parametrize("terminal", ["done", "cancelled", "closed"])
def test_preview_blocks_every_preempt_status(monkeypatch, terminal):
    """``closed`` is terminal to ``set_status`` too, so a preview promising a
    start there would describe a dispatch that does nothing."""
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": terminal,
            "dbos_workflow_id": None,
        },
    )
    r = _preview()
    assert r.will_start is False
    assert r.blocked_reason is DispatchBlockedReason.TERMINAL_STATUS


@pytest.mark.parametrize("terminal", ["done", "cancelled", "closed"])
def test_dispatch_refuses_a_terminal_issue_with_a_typed_409(monkeypatch, terminal):
    """Since ``set_status`` stopped overwriting terminal statuses, dispatching
    a terminal issue started a workflow that preempted itself and answered
    200 — a silent no-op. The endpoint now says why, in ``detail.code`` (the
    ErrorResponse envelope carries a dict detail through as ``details``)."""
    _setup(
        monkeypatch,
        {
            "id": 1,
            "assignee_agent_id": "agent-1",
            "status": terminal,
            "dbos_workflow_id": None,
        },
    )
    started: list[int] = []

    async def _fake_start(issue_id):  # noqa: ANN001, ANN202
        started.append(issue_id)
        return "wf"

    monkeypatch.setattr(issues_router, "_start_execute_issue", _fake_start)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(issues_router.dispatch_issue(1, _AUTH))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "issue_terminal"
    assert exc_info.value.detail["status"] == terminal
    assert started == []


def test_dispatch_still_starts_a_live_issue(monkeypatch):
    row = {
        "id": 1,
        "assignee_agent_id": "agent-1",
        "status": "todo",
        "dbos_workflow_id": None,
    }
    _setup(monkeypatch, row)
    started: list[int] = []

    async def _fake_start(issue_id):  # noqa: ANN001, ANN202
        started.append(issue_id)
        return "wf"

    monkeypatch.setattr(issues_router, "_start_execute_issue", _fake_start)
    monkeypatch.setattr(issues_router, "_normalise_uuid_strs", lambda r: r)
    monkeypatch.setattr(
        issues_router, "Issue", SimpleNamespace(model_validate=lambda r: r)
    )

    assert asyncio.run(issues_router.dispatch_issue(1, _AUTH)) == row
    assert started == [1]


def test_router_terminal_set_is_the_preempt_set():
    from app.workflows.issue_lifecycle import PREEMPT_STATUSES

    assert issues_router.DISPATCH_TERMINAL_STATUSES == PREEMPT_STATUSES
