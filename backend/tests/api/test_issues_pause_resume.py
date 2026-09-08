"""Phase 2a Task 5: POST /issues/{id}/pause and /resume — target-level pause.

Endpoints are exercised directly (same as the issue_messages diversion tests):
the router's module-level auth/module dependencies need a full app to
resolve, and everything the endpoints touch is injected here."""

from __future__ import annotations

import datetime as dt
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

r = importlib.import_module("app.api.issues_router")

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=UUID(ME))
NOW = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)


def _issue(**kw):
    base = {
        "id": 7,
        "status": "in_progress",
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "ai_session_id": 55,
        "paused_at": None,
        "execution_state": {},
        "execution_locked_at": None,
        "dbos_workflow_id": "issue-7-old",
    }
    base.update(kw)
    return base


@pytest.fixture
def wired(monkeypatch):
    """Everything the two endpoints touch, as a bag of mocks."""
    issue_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=_issue()),
        set_paused_at=AsyncMock(return_value=_issue(paused_at=NOW)),
    )
    monkeypatch.setattr(r, "issue_repository", issue_repo)
    monkeypatch.setattr(r, "is_issue_visible", AsyncMock(return_value=True))

    runs_repo = SimpleNamespace(
        running_root_run_id=AsyncMock(return_value=None),
        request_pause=AsyncMock(return_value=True),
        clear_pause_request=AsyncMock(return_value=True),
        list_for_issue=AsyncMock(return_value=[]),
    )
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: runs_repo)

    inbox_repo = SimpleNamespace(pending_count=AsyncMock(return_value=0))
    import app.repositories.agent_run_inbox_repository as inbox_mod

    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: inbox_repo)

    merge = AsyncMock()
    import app.services.issues.execution_state as es

    monkeypatch.setattr(es, "merge_execution_state", merge)

    from app.services.infra import dbos_orchestrator

    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)
    dispatch = MagicMock()
    monkeypatch.setattr(r, "_dispatch_execute_issue", dispatch)
    persist = AsyncMock()
    monkeypatch.setattr(r, "_persist_workflow_id", persist)
    return SimpleNamespace(
        issue_repo=issue_repo,
        runs=runs_repo,
        inbox=inbox_repo,
        merge=merge,
        dispatch=dispatch,
        persist=persist,
    )


# ── pause ──────────────────────────────────────────────────────────────────


async def test_pause_stamps_paused_at_and_requests_pause_on_the_running_root_run(
    wired,
):
    wired.runs.running_root_run_id.return_value = 31
    out = await r.pause_issue(7, AUTH)
    assert out.issue_id == "7" and out.run_id == "31"
    assert out.paused_at == NOW
    value = wired.issue_repo.set_paused_at.await_args.args
    assert value[0] == 7 and isinstance(value[1], dt.datetime) and value[1].tzinfo
    wired.runs.running_root_run_id.assert_awaited_once_with(
        issue_id=7, conversation_id=55
    )
    wired.runs.request_pause.assert_awaited_once_with(31)


async def test_pause_with_nothing_running_only_stamps(wired):
    out = await r.pause_issue(7, AUTH)
    assert out.run_id is None and out.issue_id == "7"
    wired.runs.request_pause.assert_not_awaited()


async def test_pause_twice_is_409_already_paused(wired):
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    with pytest.raises(HTTPException) as ei:
        await r.pause_issue(7, AUTH)
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "already_paused"
    wired.issue_repo.set_paused_at.assert_not_awaited()


async def test_pause_without_a_session_skips_the_conversation_key(wired):
    wired.issue_repo.get_by_id.return_value = _issue(ai_session_id=None)
    await r.pause_issue(7, AUTH)
    wired.runs.running_root_run_id.assert_awaited_once_with(
        issue_id=7, conversation_id=None
    )


# ── resume ─────────────────────────────────────────────────────────────────


async def test_resume_with_pending_inbox_items_redispatches(wired):
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.inbox.pending_count.return_value = 2
    out = await r.resume_issue(7, AUTH)
    assert out.issue_id == "7" and out.dispatched is True
    assert out.reason == "dispatched"
    assert out.workflow_id and out.workflow_id.startswith("issue-7-")
    wired.issue_repo.set_paused_at.assert_awaited_once_with(7, None)
    wired.inbox.pending_count.assert_awaited_once_with(target_kind="issue", target_id=7)
    # resumed_from_run_id is written BEFORE the dispatch, as the marker of a
    # resumed dispatch (None here: no prior run ended paused)
    wired.merge.assert_awaited_once_with(7, {"resumed_from_run_id": None})
    wired.dispatch.assert_called_once_with(7, out.workflow_id)
    wired.persist.assert_awaited_once_with(7, out.workflow_id)


async def test_resume_after_a_paused_run_redispatches_with_its_id(wired):
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.runs.list_for_issue.return_value = [
        {
            "id": 31,
            "status": "completed",
            "metadata_json": {"view": {"ended": {"reason": "paused"}}},
        }
    ]
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is True
    wired.runs.list_for_issue.assert_awaited_once_with(
        issue_id=7, conversation_id=55, limit=1
    )
    wired.merge.assert_awaited_once_with(7, {"resumed_from_run_id": "31"})
    wired.dispatch.assert_called_once()


async def test_resume_of_a_paused_but_idle_issue_just_clears_the_flag(wired):
    """Paused while waiting on a question (last run ended awaiting_input):
    nothing to re-run — the parked workflow is still waiting for the answer."""
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.runs.list_for_issue.return_value = [
        {"id": 30, "metadata_json": {"view": {"ended": {"reason": "awaiting_input"}}}}
    ]
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is False and out.workflow_id is None
    assert out.reason == "cleared"
    wired.issue_repo.set_paused_at.assert_awaited_once_with(7, None)
    wired.dispatch.assert_not_called()
    wired.merge.assert_not_awaited()


async def test_resume_while_the_run_has_not_observed_the_pause_withdraws_it(wired):
    """Pause requested, resume before the next step boundary: the run keeps
    going — withdraw the request instead of dispatching a second run."""
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.runs.running_root_run_id.return_value = 31
    wired.inbox.pending_count.return_value = 1
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is False and out.workflow_id is None
    assert out.run_id == "31" and out.reason == "withdrawn"
    wired.runs.clear_pause_request.assert_awaited_once_with(31)
    wired.dispatch.assert_not_called()
    wired.issue_repo.set_paused_at.assert_awaited_once_with(7, None)


async def test_resume_of_an_unpaused_issue_with_nothing_pending_is_409(wired):
    with pytest.raises(HTTPException) as ei:
        await r.resume_issue(7, AUTH)
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "not_paused"
    wired.issue_repo.set_paused_at.assert_not_awaited()
    wired.dispatch.assert_not_called()


async def test_resume_of_an_unpaused_issue_with_pending_items_redispatches(wired):
    """A run ended (not paused) after comments queued behind it: resume is
    the "run them now" button even though the issue was never paused."""
    wired.inbox.pending_count.return_value = 1
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is True
    wired.issue_repo.set_paused_at.assert_not_awaited()  # nothing to clear
    wired.dispatch.assert_called_once()


async def test_resume_needing_a_dispatch_without_dbos_is_503(wired, monkeypatch):
    from app.services.infra import dbos_orchestrator

    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: False)
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.inbox.pending_count.return_value = 1
    with pytest.raises(HTTPException) as ei:
        await r.resume_issue(7, AUTH)
    assert ei.value.status_code == 503
    # the flag is NOT cleared when the resume could not actually resume
    wired.issue_repo.set_paused_at.assert_not_awaited()


async def test_dispatch_failure_on_resume_restores_the_paused_flag(wired):
    """The flag is cleared BEFORE the dispatch (the new workflow's first
    loop iteration reads it) and put back when the dispatch fails."""
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.inbox.pending_count.return_value = 1
    wired.dispatch.side_effect = RuntimeError("queue down")
    with pytest.raises(HTTPException) as ei:
        await r.resume_issue(7, AUTH)
    assert ei.value.status_code == 500
    calls = [c.args for c in wired.issue_repo.set_paused_at.await_args_list]
    assert calls == [(7, None), (7, NOW)]


async def test_resume_clears_the_flag_before_dispatching(wired):
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.inbox.pending_count.return_value = 1
    order = []
    wired.issue_repo.set_paused_at.side_effect = lambda *a: order.append("clear")
    wired.dispatch.side_effect = lambda *a: order.append("dispatch")
    await r.resume_issue(7, AUTH)
    assert order == ["clear", "dispatch"]


# ── visibility ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint", ["pause_issue", "resume_issue"])
async def test_invisible_issue_is_404(wired, monkeypatch, endpoint):
    monkeypatch.setattr(r, "is_issue_visible", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as ei:
        await getattr(r, endpoint)(7, AUTH)
    assert ei.value.status_code == 404


@pytest.mark.parametrize("endpoint", ["pause_issue", "resume_issue"])
async def test_missing_issue_is_404(wired, endpoint):
    wired.issue_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as ei:
        await getattr(r, endpoint)(7, AUTH)
    assert ei.value.status_code == 404


def test_dispatch_endpoint_reuses_the_shared_workflow_id_persist():
    import inspect

    # /dispatch and /resume share ONE start-and-persist helper; the
    # service_role hop lives in exactly one place.
    assert "_start_execute_issue(" in inspect.getsource(r.dispatch_issue)
    assert "_start_execute_issue(" in inspect.getsource(r.resume_issue)
    start = inspect.getsource(r._start_execute_issue)
    assert "_persist_workflow_id(" in start and "SET LOCAL ROLE" not in start
    assert "SET LOCAL ROLE service_role" in inspect.getsource(r._persist_workflow_id)


# ── review findings (2026-09-08) ───────────────────────────────────────────


async def test_withdrawal_that_finds_the_run_gone_falls_through_to_dispatch(wired):
    """PauseHook fired between the two reads: "keeps going" would be a lie.
    Re-read and decide as if nothing ran — here the last run ended paused."""
    wired.issue_repo.get_by_id.return_value = _issue(paused_at=NOW)
    wired.runs.running_root_run_id.return_value = 31
    wired.runs.clear_pause_request.return_value = False
    wired.runs.list_for_issue.return_value = [
        {"id": 31, "metadata_json": {"view": {"ended": {"reason": "paused"}}}}
    ]
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is True and out.reason == "dispatched"
    wired.merge.assert_awaited_once_with(7, {"resumed_from_run_id": "31"})
    assert wired.issue_repo.get_by_id.await_count == 2  # re-read after the miss


async def test_resume_with_a_parked_workflow_does_not_dispatch_a_second_one(wired):
    """Parked on a question (lock held by the suspended workflow, no live
    run): a fresh execute_issue would lose on atomic_checkout and the UI would
    subscribe to a workflow that did nothing."""
    wired.issue_repo.get_by_id.return_value = _issue(
        paused_at=NOW,
        execution_locked_at=NOW,
        execution_state={"awaiting_input": {"question_id": "q:1:2"}},
    )
    wired.inbox.pending_count.return_value = 2
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is False and out.reason == "parked"
    assert out.workflow_id == "issue-7-old"
    wired.issue_repo.set_paused_at.assert_awaited_once_with(7, None)
    wired.dispatch.assert_not_called()
    wired.persist.assert_not_awaited()


async def test_resume_with_a_lock_but_no_marker_reports_running(wired):
    wired.issue_repo.get_by_id.return_value = _issue(execution_locked_at=NOW)
    wired.inbox.pending_count.return_value = 1
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is False and out.reason == "running"
    wired.dispatch.assert_not_called()


async def test_resume_of_an_unpaused_issue_with_a_live_run_does_not_dispatch(wired):
    """Queued comments are claimed at the live run's next step boundary; a
    second dispatch would clobber dbos_workflow_id with a dead one."""
    wired.inbox.pending_count.return_value = 1
    wired.runs.running_root_run_id.return_value = 31
    out = await r.resume_issue(7, AUTH)
    assert out.dispatched is False and out.reason == "running"
    assert out.run_id == "31"
    wired.runs.clear_pause_request.assert_not_awaited()
    wired.dispatch.assert_not_called()
    wired.issue_repo.set_paused_at.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["pause_issue", "resume_issue"])
async def test_run_state_read_failure_is_503_and_changes_nothing(wired, endpoint):
    """A failed running_root_run_id read is not "nothing running"."""
    wired.issue_repo.get_by_id.return_value = _issue(
        paused_at=NOW if endpoint == "resume_issue" else None
    )
    wired.runs.running_root_run_id.side_effect = RuntimeError("db down")
    with pytest.raises(HTTPException) as ei:
        await getattr(r, endpoint)(7, AUTH)
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "run_state_unavailable"
    wired.issue_repo.set_paused_at.assert_not_awaited()
    wired.runs.request_pause.assert_not_awaited()
    wired.dispatch.assert_not_called()
