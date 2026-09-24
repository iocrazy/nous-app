"""framework-hardening T5 — workforce stale-task reaper (task-centric).

Liveness is read from the TASK, never from ``agent_workers.heartbeat_at``: the
worker heartbeat only moves on state transitions (prod idle worker: 6 days
old), so a worker-heartbeat sweep would terminate healthy workers.

Three candidate classes (``phase IN (assigned, in_progress)`` and
``updated_at`` older than 10 minutes):

* DBOS still owns the workflow (PENDING/ENQUEUED, same version) → skip; DBOS
  recovery walks back in through ``claim_task``'s same-attempt arm.
* not owned, the task's run is terminal (or there never was one and the row
  is ``in_progress``) → ``failed`` / ``worker_lost``; an async sub-agent emits
  ``subagent_done`` on its parent so ``async_pending`` drains; worker → idle.
* not owned, ``assigned``, no run → requeue (nothing ran, nothing was billed).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.workforce import stale_tasks as st

pytestmark = pytest.mark.unit


class _Repo:
    def __init__(self, tasks, runs=None, worker=None):
        self.tasks = tasks
        self.runs = runs or {}
        self.worker = worker
        self.list_calls: list[dict[str, Any]] = []
        self.status_calls: list[dict[str, Any]] = []
        self.requeued: list[str] = []
        self.update_ok = True

    async def list_stale_active_tasks(self, *, older_than, limit):
        self.list_calls.append({"older_than": older_than, "limit": limit})
        return self.tasks[:limit]

    async def latest_run_for_task(self, task_id):
        return self.runs.get(str(task_id))

    async def update_task_status(self, **kwargs):
        self.status_calls.append(kwargs)
        return self.update_ok

    async def requeue_task(self, task_id):
        self.requeued.append(str(task_id))
        return True

    async def get_worker(self, agent_id):
        return self.worker


def _task(phase="assigned", *, kind=None, parent_run_id=None, wf="workforce-x-1"):
    payload: dict[str, Any] = {}
    if kind:
        payload["kind"] = kind
        payload["subagent_type"] = "researcher"
    if parent_run_id:
        payload["parent_run_id"] = parent_run_id
    return {
        "id": str(uuid4()),
        "agent_id": str(uuid4()),
        "lifecycle_status": phase,
        "payload": payload,
        "workforce_workflow_id": wf,
    }


@pytest.fixture
def wire(monkeypatch):
    self_cost = AsyncMock(return_value=3.5)

    def _wire(repo, *, owned=False):
        owns = AsyncMock(return_value=owned)
        emit = AsyncMock()
        idle = AsyncMock()
        monkeypatch.setattr(st, "get_agent_workforce_repository", lambda: repo)
        monkeypatch.setattr(st, "_dbos_still_owns", owns)
        monkeypatch.setattr(st, "emit_async_subagent_done", emit)
        monkeypatch.setattr(st, "_move_worker_back_to_idle", idle)
        monkeypatch.setattr(st, "_child_row_cost_cents", self_cost)
        return owns, emit, idle

    return _wire


async def test_terminal_run_fails_task_settles_parent_and_idles_worker(wire):
    task = _task(kind="subagent", parent_run_id="777")
    repo = _Repo(
        [task],
        runs={task["id"]: {"id": "9001", "status": "heartbeat_lost"}},
    )
    owns, emit, idle = wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == 1
    owns.assert_awaited_once_with("workforce-x-1")
    (call,) = repo.status_calls
    assert call["lifecycle_status"] == "failed"
    assert call["error_code"] == "worker_lost"
    assert call["only_from"] == ("assigned", "in_progress")
    assert str(call["task_id"]) == task["id"]
    emit.assert_awaited_once()
    kw = emit.await_args.kwargs
    assert kw["parent_run_id"] == "777"
    assert kw["child_run_id"] == "9001"
    assert kw["task_id"] == task["id"]
    assert kw["status"] == "failed"
    assert kw["cost_cents"] == 3.5
    assert "worker_lost" in kw["summary"]
    idle.assert_awaited_once()
    assert not repo.requeued


async def test_delegate_task_fails_without_parent_event(wire):
    task = _task(phase="in_progress")
    repo = _Repo([task], runs={task["id"]: {"id": "5", "status": "failed"}})
    _, emit, idle = wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == 1
    emit.assert_not_awaited()
    idle.assert_awaited_once()


async def test_in_progress_without_run_is_failed_not_requeued(wire):
    """Past ``in_progress`` the worker may have started billed work before the
    run row existed; re-running it is the one outcome we never pick."""
    task = _task(phase="in_progress")
    repo = _Repo([task])
    wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == 1
    assert not repo.requeued


async def test_assigned_without_run_is_requeued(wire):
    task = _task(phase="assigned")
    repo = _Repo([task])
    _, emit, idle = wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["requeued"] == 1
    assert repo.requeued == [task["id"]]
    assert not repo.status_calls
    emit.assert_not_awaited()


async def test_assigned_subagent_with_terminal_run_is_failed_not_requeued(wire):
    """A background sub-agent never moves its row past ``assigned``, so the run
    link — not the phase — is what says whether money was already spent."""
    task = _task(phase="assigned", kind="subagent", parent_run_id="1")
    repo = _Repo([task], runs={task["id"]: {"id": "2", "status": "failed"}})
    _, emit, _ = wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == 1
    assert not repo.requeued
    emit.assert_awaited_once()


async def test_workflow_still_owned_by_dbos_is_skipped(wire):
    task = _task(phase="in_progress")
    repo = _Repo([task], runs={task["id"]: {"id": "5", "status": "heartbeat_lost"}})
    _, emit, idle = wire(repo, owned=True)

    out = await st.reap_stale_workforce_tasks()

    assert out["skipped_pending"] == 1
    assert not repo.status_calls and not repo.requeued
    emit.assert_not_awaited()
    idle.assert_not_awaited()


async def test_healthy_in_progress_is_untouched(wire):
    """A long run that is simply still working: DBOS owns it and its run is
    live. Nothing is written."""
    task = _task(phase="in_progress")
    repo = _Repo([task], runs={task["id"]: {"id": "5", "status": "running"}})
    wire(repo, owned=True)

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == out["requeued"] == 0
    assert not repo.status_calls and not repo.requeued


async def test_running_run_is_left_to_the_heartbeat_sweep(wire):
    """Workflow gone but the run still says ``running``: ``mark_heartbeat_lost``
    closes it within 2 minutes; this reaper acts on the next tick."""
    task = _task(phase="in_progress")
    repo = _Repo([task], runs={task["id"]: {"id": "5", "status": "running"}})
    wire(repo, owned=False)

    out = await st.reap_stale_workforce_tasks()

    assert out["skipped_running"] == 1
    assert not repo.status_calls and not repo.requeued


async def test_threshold_and_limit_are_passed_to_the_candidate_query(wire):
    repo = _Repo([_task() for _ in range(25)])
    wire(repo)
    before = datetime.now(timezone.utc)

    out = await st.reap_stale_workforce_tasks()

    (call,) = repo.list_calls
    assert call["limit"] == 20
    cutoff = before - timedelta(minutes=10)
    assert abs((call["older_than"] - cutoff).total_seconds()) < 5
    assert out["candidates"] == 20
    assert len(repo.requeued) == 20


async def test_lost_cas_emits_nothing(wire):
    """The row finished between the scan and the write — its own completion
    already emitted ``subagent_done``; a second one would double-count."""
    task = _task(kind="subagent", parent_run_id="7")
    repo = _Repo([task], runs={task["id"]: {"id": "8", "status": "completed"}})
    repo.update_ok = False
    _, emit, idle = wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["raced"] == 1 and out["failed"] == 0
    emit.assert_not_awaited()
    idle.assert_not_awaited()


async def test_worker_already_on_another_task_is_not_idled(wire):
    task = _task(phase="in_progress")
    repo = _Repo(
        [task],
        runs={task["id"]: {"id": "5", "status": "failed"}},
        worker={"state": "working", "current_task_id": str(uuid4())},
    )
    _, _, idle = wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["failed"] == 1
    idle.assert_not_awaited()


async def test_one_bad_row_does_not_sink_the_rest(wire):
    bad, good = _task(), _task()
    repo = _Repo([bad, good])
    owns, _, _ = wire(repo)
    owns.side_effect = [RuntimeError("boom"), False]

    out = await st.reap_stale_workforce_tasks()

    assert out["errors"] == 1
    assert repo.requeued == [good["id"]]


async def test_candidate_scan_failure_is_reported_not_raised(wire):
    repo = _Repo([])
    repo.list_stale_active_tasks = AsyncMock(side_effect=RuntimeError("db down"))
    wire(repo)

    out = await st.reap_stale_workforce_tasks()

    assert out["errors"] == 1
    assert out["candidates"] == 0
