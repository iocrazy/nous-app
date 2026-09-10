"""The dispatch window: _dispatch_execute_issue returns before atomic_checkout
runs, so between them running_root_run_id AND execution_locked_at are both
empty. A fork in that window swings ai_session_id away and the workflow's own
checkout then silently returns {"skipped": True}. The marker closes it."""

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.services.issues import issue_dispatch

pytestmark = pytest.mark.unit


def _issue(at):
    return {"execution_state": {"dispatching": {"workflow_id": "wf-1", "at": at}}}


def _ns(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def _async(value, *, record=None):
    async def _call(*a, **kw):
        if record is not None:
            record.append((a, kw))
        return value

    return _call


def test_a_fresh_marker_means_busy():
    now = datetime.now(timezone.utc)
    assert issue_dispatch.is_dispatching(_issue(now.isoformat()), now=now) is True


def test_stale_null_and_malformed_markers_are_all_not_busy():
    """A crashed dispatch must not wedge the issue forever, and an unparseable
    marker is not a guard we can trust."""
    now = datetime.now(timezone.utc)
    assert (
        issue_dispatch.is_dispatching(
            _issue((now - timedelta(seconds=61)).isoformat()), now=now
        )
        is False
    )
    for state in (
        {},
        {"dispatching": None},
        {"dispatching": {}},
        {"dispatching": {"at": "not-a-time"}},
    ):
        assert (
            issue_dispatch.is_dispatching({"execution_state": state}, now=now) is False
        )
    # Exactly at the TTL is already expired: the boundary belongs to "the
    # workflow never came up", not to "still dispatching".
    assert (
        issue_dispatch.is_dispatching(
            _issue((now - timedelta(seconds=60)).isoformat()), now=now
        )
        is False
    )


@pytest.mark.asyncio
async def test_start_execute_issue_marks_before_dispatch_and_clears_on_failure(
    monkeypatch,
):
    calls = []

    async def _merge(issue_id, patch):
        calls.append(patch)

    monkeypatch.setattr(issue_dispatch, "merge_execution_state", _merge)
    # importlib + sys.modules, not ``import app.api.issues_router as m``: the
    # package re-exports the APIRouter under that very name, so the plain
    # import binds the router OBJECT. start_execute_issue resolves the module
    # the same way — patch what production actually calls.
    importlib.import_module("app.api.issues_router")
    router_mod = sys.modules["app.api.issues_router"]

    def _boom(issue_id, workflow_id):
        raise RuntimeError("dbos is down")

    monkeypatch.setattr(router_mod, "_dispatch_execute_issue", _boom)
    with pytest.raises(issue_dispatch.DispatchFailed):
        await issue_dispatch.start_execute_issue(7)
    assert list(calls[0]["dispatching"]) == ["workflow_id", "at"]
    assert calls[1] == {"dispatching": None}  # cleared, not left to rot for 60s


# ── The three readers that were blind to the window ────────────────────────


def _marker(seconds_ago: float = 0.0) -> dict:
    at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return {"dispatching": {"workflow_id": "wf-1", "at": at.isoformat()}}


class _ForkDeps:
    """Only what fork_run touches before the busy checks — the refusal must
    land before any rebuild work, so nothing further is ever reached."""

    def __init__(self, execution_state: dict):
        self.issue = {
            "id": 9,
            "status": "in_progress",
            "ai_session_id": 100,
            "hidden_at": None,
            "execution_locked_at": None,
            "dbos_workflow_id": None,
            "execution_state": execution_state,
        }
        self.events_read = False

    async def get_run(self, run_id, user_id):
        return {"id": 42, "issue_id": 9, "conversation_id": 100}

    async def get_issue(self, issue_id):
        return self.issue

    async def running_root_run_id(self, issue_id, conversation_id):
        return None

    async def list_events(self, run_id, at_seq):
        self.events_read = True
        return []


async def test_fork_refuses_while_a_dispatch_is_in_flight():
    from app.services.issues import issue_fork

    deps = _ForkDeps(_marker())
    with pytest.raises(issue_fork.ForkRejected) as ei:
        await issue_fork.fork_run(42, at_seq=2, steer=None, user_id="u", deps=deps)
    assert (ei.value.code, ei.value.status) == ("issue_busy", 409)
    assert deps.events_read is False


async def test_fork_proceeds_once_the_marker_has_expired():
    """The guard is a window, not a lock: a marker older than the TTL must not
    keep an issue unforkable forever."""
    from app.services.issues import issue_fork

    deps = _ForkDeps(_marker(seconds_ago=61))
    with pytest.raises(issue_fork.ForkRejected) as ei:
        await issue_fork.fork_run(42, at_seq=2, steer=None, user_id="u", deps=deps)
    # Got past the busy checks and refused on the boundary instead.
    assert ei.value.code == "not_a_step_boundary"
    assert deps.events_read is True


async def test_resume_refuses_while_a_dispatch_is_in_flight(monkeypatch):
    import importlib

    from fastapi import HTTPException

    r = importlib.import_module("app.api.issues_router")
    from tests.api.test_issues_pause_resume import AUTH, _issue

    issue = _issue(paused_at="2026-09-08T12:00:00+00:00", execution_state=_marker())
    monkeypatch.setattr(r, "is_issue_visible", _async(True))
    monkeypatch.setattr(
        r, "issue_repository", _ns(get_by_id=_async(issue), set_paused_at=_async(issue))
    )
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: _ns(pending_count=_async(1)),
    )
    dispatched = []
    monkeypatch.setattr(r, "_start_execute_issue", _async("wf", record=dispatched))

    with pytest.raises(HTTPException) as ei:
        await r.resume_issue(7, AUTH)
    assert ei.value.status_code == 409
    assert ei.value.detail == {
        "code": "issue_busy",
        "message": "a dispatch is in flight",
    }
    assert dispatched == []  # never a second dispatch into the same window


async def test_a_comment_lands_on_the_inbox_while_a_dispatch_is_in_flight(monkeypatch):
    """The window's third reader. running_root_run_id is None (no run row yet)
    and the issue is not paused — before the marker this fell through to the
    wake path and started a SECOND turn."""
    import importlib
    from unittest.mock import AsyncMock, MagicMock

    importlib.import_module("app.api.issue_messages_router")
    import sys

    r = sys.modules["app.api.issue_messages_router"]

    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    enqueue = AsyncMock(return_value={"id": 310819108761499})
    monkeypatch.setattr(
        inbox_mod, "get_agent_run_inbox_repository", lambda: _ns(enqueue=enqueue)
    )
    store = MagicMock()
    store.append_user_message = AsyncMock()
    store_cls = MagicMock(return_value=store)
    store_cls.display_attachments = staticmethod(lambda a: a)
    monkeypatch.setattr(r, "ConversationsAiStore", store_cls)

    inbox_id = await r._divert_to_inbox_if_running(
        5,
        "55",
        "11111111-1111-1111-1111-111111111111",
        _ns(user_id="11111111-1111-1111-1111-111111111111"),
        "hold on",
        None,
        paused=False,
        issue_row={"id": 5, "execution_state": _marker()},
    )
    assert inbox_id == "310819108761499"
    enqueue.assert_awaited_once()


async def test_an_idle_issue_still_falls_through_to_the_wake_path(monkeypatch):
    """Negative control: without a marker the diversion must not fire, or every
    comment on an idle issue would silently queue instead of waking a turn."""
    import importlib
    import sys

    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    assert (
        await r._divert_to_inbox_if_running(
            5,
            "55",
            "11111111-1111-1111-1111-111111111111",
            _ns(user_id="11111111-1111-1111-1111-111111111111"),
            "hold on",
            None,
            paused=False,
            issue_row={"id": 5, "execution_state": {}},
        )
        is None
    )
