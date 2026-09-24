"""Stale workforce task reaper — task-centric, run by the minute sweeper.

Why task-centric
================
``agent_workers.heartbeat_at`` is NOT a liveness signal: it only moves on a
state transition (``update_worker_state(bump_heartbeat=True)``), and nothing
calls ``repo.heartbeat``. In production the one idle worker's heartbeat was
six days old while perfectly healthy. A worker-heartbeat sweep would therefore
terminate healthy workers, so this module never reads it (and
``list_stale_workers`` stays unwired until workers heartbeat while working).

The signals that DO exist are on the task:

* the DBOS workflow that owns it (``metadata.workforce_workflow_id``,
  ``workforce-<task>-<attempt>``) — DBOS recovers a dead worker's workflow and
  walks back in through ``claim_task``'s same-attempt arm, so while that
  workflow is PENDING/ENQUEUED (same app version) the task is DBOS's to finish;
* the task's run (``agent_runs.task_id``, mig 282) — it carries a real 15 s
  heartbeat, and ``mark_heartbeat_lost_step`` flips it to ``heartbeat_lost``
  two minutes after the worker dies.

Rules
=====
Candidates: ``phase IN ('assigned','in_progress')`` and ``updated_at`` older
than ``threshold_min`` (default 10 = 5x the run sweeper's 2-minute window),
oldest first. Up to ``scan_limit`` (200) rows are read and at most ``limit``
(20) are acted on: skipped rows stay stale, so a scan as narrow as the action
cap would let them block the orphans behind them forever.

1. DBOS still owns the workflow → skip. Racing recovery would fail a task DBOS
   is about to finish.
2. The run is still ``running`` → skip. The run sweeper closes it first (with
   its own typed ``heartbeat_lost``); this reaper acts on a later tick. Runs
   are never closed here.
3. No run and ``assigned`` → requeue. Nothing ran and nothing was billed, so a
   retry is safe; ``requeue_task`` keeps ``dispatch_attempt`` so the next
   workflow id is fresh. "No run" means no ``agent_runs`` row carries this
   ``task_id``: a background child in flight at the deploy that introduced
   the link has an unlinked run, so it lands HERE and is re-run once (the
   cost is the interrupted round's spend).
4. Otherwise (run terminal, or ``in_progress`` with no run — the worker may
   have started billed work before the run row existed) → ``failed`` /
   ``error_code=worker_lost``, CAS on the phase. An async sub-agent also gets
   ``subagent_done`` on its parent through the SAME function the worker's
   normal completion uses, so ``async_pending`` drains and the tree settles
   now instead of at the 2-hour forced settle (which would also log a
   misleading "never materialised" WARNING). The worker goes back to idle.
   Before emitting, the parent is checked for a ``subagent_done`` already
   carrying this ``task_id`` — the worker emits it before finalising the row,
   and a lost finalise leaves exactly this shape behind a SUCCESS workflow.
   If it is there the row is closed to match it (``done``/``failed``) and no
   second event is written: ``fold_done`` has no per-child dedup.

Every row is handled in its own try/except: one bad row is logged and
counted, and the tick itself never fails.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from loguru import logger

from app.repositories.agent_workforce_repository import (
    AgentWorkforceRepository,
    get_agent_workforce_repository,
)
from app.services.workforce.agent_worker import (
    _child_row_cost_cents,
    _move_worker_back_to_idle,
    emit_async_subagent_done,
)
from app.workflows.workflow_health_sweeper import _dbos_still_owns

STALE_TASK_THRESHOLD_MIN = 10
STALE_TASK_LIMIT = 20
#: Rows READ per tick, wider than ``STALE_TASK_LIMIT`` (rows ACTED on). Skipped
#: rows (a live run, a workflow DBOS still owns — an AskUser park can hold one
#: for hours) stay stale, and a scan as narrow as the action cap would let 20
#: of them take every slot forever while the real orphans behind them wait.
STALE_TASK_SCAN_LIMIT = 200
WORKER_LOST = "worker_lost"
_ACTIVE_PHASES = ("assigned", "in_progress")

#: Outcomes that count against ``limit``: they wrote something (or tried to).
_ACTIONS = ("done", "failed", "requeued", "raced")

_OUTCOMES = (
    "done",
    "failed",
    "requeued",
    "skipped_pending",
    "skipped_running",
    "raced",
)


async def reap_stale_workforce_tasks(
    threshold_min: int = STALE_TASK_THRESHOLD_MIN,
    limit: int = STALE_TASK_LIMIT,
    scan_limit: int = STALE_TASK_SCAN_LIMIT,
) -> dict[str, int]:
    """One pass over stale workforce tasks. Reads up to ``scan_limit`` rows and
    acts on at most ``limit`` of them (errors count as actions too, so a
    systematically failing write cannot turn one tick into 200 attempts).
    Returns per-outcome counts plus ``candidates`` and ``errors``; never
    raises."""
    counts: dict[str, int] = {"candidates": 0, "errors": 0}
    counts.update({k: 0 for k in _OUTCOMES})
    repo = get_agent_workforce_repository()
    older_than = datetime.now(timezone.utc) - timedelta(minutes=threshold_min)
    try:
        tasks = await repo.list_stale_active_tasks(
            older_than=older_than, limit=scan_limit
        )
    except Exception as err:  # noqa: BLE001 — a scan that cannot read has not
        # proved nothing is stuck; say so and try again next tick.
        logger.error(f"[stale-tasks] candidate scan failed: {err}")
        counts["errors"] += 1
        return counts

    counts["candidates"] = len(tasks)
    for task in tasks:
        if counts["errors"] + sum(counts[k] for k in _ACTIONS) >= limit:
            break
        try:
            outcome = await _reap_one(repo, task)
        except Exception as err:  # noqa: BLE001 — one row must not sink the tick
            logger.exception(f"[stale-tasks] task {task.get('id')} failed: {err}")
            counts["errors"] += 1
            continue
        counts[outcome] += 1
    return counts


async def _reap_one(repo: AgentWorkforceRepository, task: dict[str, Any]) -> str:
    task_id = str(task["id"])
    phase = task.get("lifecycle_status")

    # ``_dbos_still_owns`` fails CLOSED (a status read error counts as owned),
    # so an unreadable workflow is skipped, never failed.
    if await _dbos_still_owns(task.get("workforce_workflow_id")):
        return "skipped_pending"

    run = await repo.latest_run_for_task(task_id)
    if run is not None and run.get("status") == "running":
        return "skipped_running"

    if run is None and phase == "assigned":
        if await repo.requeue_task(UUID(task_id)):
            logger.warning(
                f"[stale-tasks] requeued task {task_id}: workflow "
                f"{task.get('workforce_workflow_id')} is gone and no run started"
            )
            return "requeued"
        return "raced"

    return await _fail_worker_lost(repo, task, run)


async def _fail_worker_lost(
    repo: AgentWorkforceRepository,
    task: dict[str, Any],
    run: dict[str, Any] | None,
) -> str:
    task_id = str(task["id"])
    run_id = run.get("id") if run else None
    payload = task.get("payload") or {}
    parent_run_id = payload.get("parent_run_id")
    is_async_child = (payload.get("kind") or "") == "subagent" and bool(parent_run_id)

    # Did the parent already hear? The worker emits ``subagent_done`` BEFORE
    # it finalises the row, and ``_finalise_subagent_task`` only logs a failed
    # UPDATE — so a row left ``assigned`` behind a SUCCESS workflow is the
    # expected shape of that one lost write, not of a dead worker. Emitting a
    # second event would count the child twice and flip ``last.status``. When
    # the event is there, finalise the row from ITS status and emit nothing.
    already: str | None = None
    if is_async_child:
        # A read error propagates: "could not look" is not "not emitted". The
        # per-row handler logs it and the row is retried next tick.
        already = await repo.subagent_done_status(
            parent_run_id=str(parent_run_id), task_id=task_id
        )
    if already is not None:
        return await _finalise_from_existing_event(repo, task, already)

    message = (
        f"worker lost: workflow {task.get('workforce_workflow_id')} is no longer "
        f"owned by DBOS and the task stayed {task.get('lifecycle_status')} "
        f"(run {run_id or 'none'}: {run.get('status') if run else 'never started'})"
    )
    written = await repo.update_task_status(
        task_id=UUID(task_id),
        lifecycle_status="failed",
        error_code=WORKER_LOST,
        error_message=message[:500],
        only_from=_ACTIVE_PHASES,
    )
    if not written:
        # Finished (or requeued) between the scan and now — its own path
        # already did the bookkeeping; a second subagent_done would
        # double-count on the parent.
        return "raced"
    logger.warning(f"[stale-tasks] task {task_id} → failed/{WORKER_LOST}: {message}")

    # ``run_id`` is always set here for a sub-agent: a background child never
    # leaves ``assigned``, and "assigned + no run" was requeued above. That
    # includes children that were in flight at the deploy introducing the
    # run↔task link — their run carries no ``task_id``, so they look unstarted
    # and are REQUEUED (re-run once; the cost is the interrupted round's
    # spend), not closed here without a child id.
    if is_async_child:
        await emit_async_subagent_done(
            parent_run_id=str(parent_run_id),
            task_id=task_id,
            child_run_id=run_id,
            subagent_type=payload.get("subagent_type"),
            status="failed",
            summary=f"{WORKER_LOST}: the worker running this sub-agent died",
            # Same fallback the worker's crash branch uses when no envelope
            # carries the cost: the child row's display column, 0.0 (logged)
            # when there is no row to ask.
            cost_cents=await _child_row_cost_cents(run_id, UUID(task_id)),
            byok_cents=0,
            tokens_used=0,
            duration_ms=None,
        )

    await _idle_worker_if_ours(repo, task)
    return "failed"


async def _finalise_from_existing_event(
    repo: AgentWorkforceRepository, task: dict[str, Any], event_status: str
) -> str:
    """The parent already has this child's ``subagent_done``; only the row's
    own finalise was lost. Close the row to match the event — never emit."""
    task_id = str(task["id"])
    ok = event_status == "success"
    written = await repo.update_task_status(
        task_id=UUID(task_id),
        lifecycle_status="done" if ok else "failed",
        **(
            {}
            if ok
            else {
                "error_code": WORKER_LOST,
                "error_message": (
                    f"row was never finalised; the parent already recorded "
                    f"subagent_done status={event_status or 'unknown'}"
                ),
            }
        ),
        only_from=_ACTIVE_PHASES,
    )
    if not written:
        return "raced"
    logger.warning(
        f"[stale-tasks] task {task_id}: parent already has subagent_done "
        f"({event_status or 'no status'}); row finalised "
        f"{'done' if ok else 'failed'} without a second event"
    )
    await _idle_worker_if_ours(repo, task)
    return "done" if ok else "failed"


async def _idle_worker_if_ours(
    repo: AgentWorkforceRepository, task: dict[str, Any]
) -> None:
    """Move the worker back to idle — unless it has visibly moved on to a
    different task, in which case its state is not ours to reset."""
    agent_id_raw = task.get("agent_id")
    if not agent_id_raw:
        return
    agent_id = UUID(str(agent_id_raw))
    worker = await repo.get_worker(agent_id)
    current = (worker or {}).get("current_task_id")
    if current and str(current) != str(task["id"]):
        return
    await _move_worker_back_to_idle(
        agent_id, UUID(str(task["id"])), trigger="task_completed"
    )
