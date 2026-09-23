"""Defects C (part 1) and F: cancelling an issue stops the work on it.

S4 (2026-09-23 prod): ``/transition → cancelled`` left the live root run's
``cancel_requested`` false, so step 2 started 36 s after the cancel. S7b: a
workflow parked on the budget question stayed PENDING for the 72 h recv TTL
after the budget "Cancel". One hook on the repository's cancel edge covers
both entry points (the endpoint and the budget answer)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories import issue_repository as repo_mod
from app.services.issues import cancel_live_work as clw

pytestmark = pytest.mark.unit

WF = "issue-7-abc"
PARKED = {
    "id": 7,
    "status": "cancelled",
    "ai_session_id": 55,
    "dbos_workflow_id": WF,
    "execution_locked_at": "2026-09-23T00:00:00+00:00",
    "execution_state": {"awaiting_input": {"question_id": "budget:1", "run_id": 1}},
}
RUNNING = {"id": 7, "status": "cancelled", "ai_session_id": 55}
_REPO_HOOKS = (
    "_fire_subissue_barrier",
    "_fire_pipeline_relay",
    "_fire_stage_node_sync",
)


class _Runs:
    def __init__(self, run_id=None, *, read_error=None, requested=True):
        self.running_root_run_id = AsyncMock(
            return_value=run_id, side_effect=read_error
        )
        self.request_cancel = AsyncMock(return_value=requested)


@pytest.fixture
def runs(monkeypatch):
    holder = {"runs": _Runs()}
    monkeypatch.setattr(clw, "_runs_repo", lambda: holder["runs"])
    return holder


@pytest.fixture
def release(monkeypatch):
    rel = AsyncMock()
    monkeypatch.setattr(clw, "_release_parked_workflow", rel)
    return rel


@pytest.fixture
def wf_status(monkeypatch):
    st = AsyncMock(return_value="PENDING")
    monkeypatch.setattr(clw, "_workflow_status", st)
    return st


# ── the edge ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prev",
    ["backlog", "todo", "in_progress", "needs_followup", "in_review", "blocked", None],
)
def test_cancel_edge_fires_from_every_live_status(prev):
    assert clw.is_cancel_edge(prev, "cancelled") is True


@pytest.mark.parametrize(
    "prev,new",
    [
        ("in_progress", "done"),  # ruling 3: stage closes land done on mirrors
        ("cancelled", "cancelled"),  # repeat save / budget double-send
        ("done", "cancelled"),
        ("in_progress", "in_review"),
    ],
)
def test_cancel_edge_does_not_fire(prev, new):
    assert clw.is_cancel_edge(prev, new) is False


# ── (a) the running root run ────────────────────────────────────────────────


async def test_running_root_run_is_flagged_without_an_owner(runs, release, wf_status):
    runs["runs"] = _Runs(run_id=901)
    await clw.stop_live_work_for_cancel(7, issue=RUNNING)
    runs["runs"].running_root_run_id.assert_awaited_once_with(
        issue_id=7, conversation_id=55
    )
    # Authorised at the target (the transition), not run ownership — a team
    # member may cancel an issue whose run the owner started.
    runs["runs"].request_cancel.assert_awaited_once_with("901")
    release.assert_not_awaited()


async def test_no_running_run_flags_nothing(runs, release, wf_status):
    await clw.stop_live_work_for_cancel(7, issue=RUNNING)
    runs["runs"].request_cancel.assert_not_awaited()


async def test_a_failed_run_read_is_logged_and_the_rest_still_runs(
    runs, release, wf_status
):
    runs["runs"] = _Runs(read_error=RuntimeError("db down"))
    with patch.object(clw.logger, "error") as err:
        await clw.stop_live_work_for_cancel(7, issue=PARKED)
    assert any("db down" in str(c.args[0]) for c in err.call_args_list)
    release.assert_awaited_once_with(WF)  # one failure does not skip the next


# ── (b) the parked workflow (defect F) ──────────────────────────────────────


async def test_parked_pending_workflow_is_released(runs, release, wf_status):
    await clw.stop_live_work_for_cancel(7, issue=PARKED)
    wf_status.assert_awaited_once_with(WF)
    release.assert_awaited_once_with(WF)


@pytest.mark.parametrize(
    "change",
    [
        {"execution_state": {}},  # no marker
        {"execution_locked_at": None},  # nobody holds the lock
        {"dbos_workflow_id": None},
        {
            "execution_state": {
                "awaiting_input": {"question_id": "q", "answered_at": "2026-09-23"}
            }
        },  # already answered: the workflow is resuming, not parked
    ],
)
async def test_not_parked_is_not_released(runs, release, wf_status, change):
    await clw.stop_live_work_for_cancel(7, issue={**PARKED, **change})
    release.assert_not_awaited()


@pytest.mark.parametrize("status", ["SUCCESS", "CANCELLED", "ERROR", None])
async def test_a_workflow_that_already_ended_is_left_alone(
    runs, release, wf_status, status
):
    wf_status.return_value = status
    await clw.stop_live_work_for_cancel(7, issue=PARKED)
    release.assert_not_awaited()


async def test_an_unreadable_workflow_status_still_releases(runs, release, wf_status):
    """Marker + lock is the fork's proven parked predicate; an unreadable
    engine status must not strand the workflow for the 72 h TTL."""
    wf_status.side_effect = RuntimeError("dbos down")
    await clw.stop_live_work_for_cancel(7, issue=PARKED)
    release.assert_awaited_once_with(WF)


async def test_a_failed_release_is_logged_not_raised(runs, release, wf_status):
    release.side_effect = RuntimeError("cancel failed")
    with patch.object(clw.logger, "error") as err:
        await clw.stop_live_work_for_cancel(7, issue=PARKED)
    assert any("cancel failed" in str(c.args[0]) for c in err.call_args_list)


async def test_a_failed_release_is_left_for_the_worker_reaper(runs, release, wf_status):
    """Hotfix-2 defect H: a release that could not cancel keeps the marker, so
    the log must say the worker reaper finishes it (not a silent strand)."""
    release.side_effect = RuntimeError("no DBOS handle")
    with patch.object(clw.logger, "error") as err:
        await clw.stop_live_work_for_cancel(7, issue=PARKED)
    msgs = [str(c.args[0]) for c in err.call_args_list]
    assert any("no DBOS handle" in m and "reaper" in m for m in msgs), msgs


# ── the status read in the API process (defect H) ──────────────────────────


class _Status:
    status = "PENDING"


class _Handle:
    async def get_status(self):
        return _Status()


class _StatusClient:
    def __init__(self, *, missing=False):
        self.missing = missing
        self.asked = []

    async def retrieve_workflow_async(self, workflow_id):
        self.asked.append(workflow_id)
        if self.missing:
            from dbos._error import DBOSNonExistentWorkflowError

            raise DBOSNonExistentWorkflowError("target", workflow_id)
        return _Handle()


def _api_shape(monkeypatch, client):
    from app.services.infra import dbos_orchestrator as orch

    monkeypatch.setattr(orch, "_dbos", None)
    monkeypatch.setattr(orch, "_launched", False)
    monkeypatch.setattr(orch, "_client", client)


async def test_status_read_goes_through_the_client_in_the_api_process(monkeypatch):
    from dbos._error import DBOSException

    client = _StatusClient()
    _api_shape(monkeypatch, client)
    boom = AsyncMock(side_effect=DBOSException("No DBOS was created yet"))
    with patch("dbos.DBOS.get_workflow_status_async", boom):
        assert await clw._workflow_status(WF) == "PENDING"
    assert client.asked == [WF]
    boom.assert_not_awaited()


async def test_status_read_of_an_unknown_workflow_is_none(monkeypatch):
    _api_shape(monkeypatch, _StatusClient(missing=True))
    assert await clw._workflow_status(WF) is None


async def test_marker_stored_as_a_json_string_is_read(runs, release, wf_status):
    row = {**PARKED, "execution_state": json.dumps(PARKED["execution_state"])}
    await clw.stop_live_work_for_cancel(7, issue=row)
    release.assert_awaited_once_with(WF)


async def test_loads_the_issue_when_no_row_is_given(
    monkeypatch, runs, release, wf_status
):
    load = AsyncMock(return_value=PARKED)
    monkeypatch.setattr(clw, "_load_issue", load)
    await clw.stop_live_work_for_cancel(7)
    load.assert_awaited_once_with(7)
    release.assert_awaited_once_with(WF)


# ── the repository seam ─────────────────────────────────────────────────────


@pytest.fixture
def repo(monkeypatch):
    r = repo_mod.IssueRepository()
    r.update = AsyncMock(side_effect=lambda iid, p: {**PARKED, "id": iid, **p})
    for hook in _REPO_HOOKS:
        monkeypatch.setattr(repo_mod, hook, AsyncMock())
    stop = AsyncMock()
    monkeypatch.setattr(clw, "stop_live_work_for_cancel", stop)
    r.stop = stop
    return r


@pytest.mark.parametrize("prev", ["todo", "in_progress", "needs_followup"])
async def test_transition_to_cancelled_stops_live_work(repo, prev):
    repo.get_by_id = AsyncMock(return_value={"id": 7, "status": prev})
    await repo.transition_status(7, "cancelled")
    repo.stop.assert_awaited_once()
    assert repo.stop.await_args.args == (7,)
    # the freshly written row rides along — no second read of the issue
    assert repo.stop.await_args.kwargs["issue"]["status"] == "cancelled"


@pytest.mark.parametrize(
    "prev,new", [("in_progress", "done"), ("cancelled", "cancelled")]
)
async def test_other_transitions_do_not_stop_work(repo, prev, new):
    repo.get_by_id = AsyncMock(return_value={"id": 7, "status": prev})
    await repo.transition_status(7, new)
    repo.stop.assert_not_awaited()


async def test_a_failing_hook_never_fails_the_transition(repo):
    repo.get_by_id = AsyncMock(return_value={"id": 7, "status": "in_progress"})
    repo.stop.side_effect = RuntimeError("boom")
    with patch.object(repo_mod.logger, "error") as err:
        row = await repo.transition_status(7, "cancelled")
    assert row["status"] == "cancelled"
    assert err.called


# ── defect F: the budget question's Cancel goes through the same seam ──────


async def test_budget_cancel_releases_the_parked_workflow(
    monkeypatch, runs, release, wf_status
):
    from app.services.ai.runner.question_kinds import budget

    r = repo_mod.issue_repository
    monkeypatch.setattr(
        r, "get_by_id", AsyncMock(return_value={**PARKED, "status": "needs_followup"})
    )
    monkeypatch.setattr(
        r, "update", AsyncMock(side_effect=lambda iid, p: {**PARKED, **p})
    )
    for hook in _REPO_HOOKS:
        monkeypatch.setattr(repo_mod, hook, AsyncMock())
    monkeypatch.setattr(
        "app.services.issues.execution_state.merge_execution_state", AsyncMock()
    )

    await budget.on_answer({"id": 7}, "Cancel", ctx=None)

    release.assert_awaited_once_with(WF)


def test_cancel_terminal_set_is_the_preempt_set():
    """Two copies of one fact: the cancel edge's notion of "already terminal"
    and ``set_status``'s refusal set. If they drift, a cancel from a status
    one side calls terminal fires (or skips) the stop hook on a write the
    other side would (or would not) make."""
    from app.services.issues.cancel_live_work import TERMINAL_STATUSES
    from app.workflows.issue_lifecycle import PREEMPT_STATUSES

    assert TERMINAL_STATUSES == PREEMPT_STATUSES
