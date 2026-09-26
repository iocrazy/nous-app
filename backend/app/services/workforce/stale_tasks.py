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

from app.repositories.agent_run_inbox_repository import get_agent_run_inbox_repository
from app.repositories.agent_workforce_repository import (
    AgentWorkforceRepository,
    get_agent_workforce_repository,
)
from app.services.workforce.agent_worker import (
    _child_row_cost_cents,
    _move_worker_back_to_idle,
    emit_async_subagent_done,
)
from app.services.workforce.settle import (
    SettleReason,
    settle_reason_for_envelope,
    settle_reason_for_lost_run,
)
from app.services.workforce.subagent_delivery import deliver_subagent_result
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
    Returns per-outcome counts plus ``candidates`` and ``errors``, and
    ``wake_orders`` — one ``{"task_id", "idle_dispatch"}`` per lost async child
    whose result was filed on an issue (fh4 E2b). The caller is a STEP: it
    returns the orders to the sweeper workflow BODY, which dispatches them
    with ``agent_workforce._dispatch_idle_wake``. Never raises."""
    counts: dict[str, Any] = {"candidates": 0, "errors": 0, "wake_orders": []}
    counts.update({k: 0 for k in _OUTCOMES})
    wake_orders: list[dict[str, Any]] = []
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
            outcome, order = await _reap_one(repo, task)
        except Exception as err:  # noqa: BLE001 — one row must not sink the tick
            logger.exception(f"[stale-tasks] task {task.get('id')} failed: {err}")
            counts["errors"] += 1
            continue
        counts[outcome] += 1
        if order is not None:
            wake_orders.append(order)
    return {**counts, "wake_orders": wake_orders}


#: One reaped row: its outcome, and the wake order it owes (or None).
_Reaped = tuple[str, dict[str, Any] | None]


async def _reap_one(repo: AgentWorkforceRepository, task: dict[str, Any]) -> _Reaped:
    task_id = str(task["id"])
    phase = task.get("lifecycle_status")

    # ``_dbos_still_owns`` fails CLOSED (a status read error counts as owned),
    # so an unreadable workflow is skipped, never failed.
    if await _dbos_still_owns(task.get("workforce_workflow_id")):
        return "skipped_pending", None

    run = await repo.latest_run_for_task(task_id)
    if run is not None and run.get("status") == "running":
        return "skipped_running", None

    if run is None and phase == "assigned":
        if await repo.requeue_task(UUID(task_id)):
            logger.warning(
                f"[stale-tasks] requeued task {task_id}: workflow "
                f"{task.get('workforce_workflow_id')} is gone and no run started"
            )
            return "requeued", None
        return "raced", None

    return await _fail_worker_lost(repo, task, run)


async def _fail_worker_lost(
    repo: AgentWorkforceRepository,
    task: dict[str, Any],
    run: dict[str, Any] | None,
) -> _Reaped:
    task_id = str(task["id"])
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
        return await _finalise_from_existing_event(repo, task, already), None
    if is_async_child:
        return await _close_lost_async_child(repo, task, run)

    written = await repo.update_task_status(
        task_id=UUID(task_id),
        lifecycle_status="failed",
        error_code=WORKER_LOST,
        error_message=_lost_message(task, run)[:500],
        only_from=_ACTIVE_PHASES,
    )
    if not written:
        # Finished (or requeued) between the scan and now — its own path
        # already did the bookkeeping.
        return "raced", None
    logger.warning(
        f"[stale-tasks] task {task_id} → failed/{WORKER_LOST}: "
        f"{_lost_message(task, run)}"
    )
    await _idle_worker_if_ours(repo, task)
    return "failed", None


def _lost_message(task: dict[str, Any], run: dict[str, Any] | None) -> str:
    return (
        f"worker lost: workflow {task.get('workforce_workflow_id')} is no longer "
        f"owned by DBOS and the task stayed {task.get('lifecycle_status')} "
        f"(run {(run or {}).get('id') or 'none'}: "
        f"{run.get('status') if run else 'never started'})"
    )


_LOST_SUMMARY = {
    SettleReason.LOST: f"{WORKER_LOST}: the worker running this sub-agent died",
    SettleReason.TEARDOWN: (
        "worker_shutdown: the worker was shut down while this sub-agent ran"
    ),
}

_EVENT_STATUS_TO_LIFECYCLE = {"success": "done", "cancelled": "cancelled"}


def _filed_content(
    ours: dict[str, Any], stored: dict[str, Any] | None
) -> dict[str, Any]:
    """The content that actually reached the inbox: ours, or the first
    writer's. A row filed before fh4 carries no ``settle_reason``; it was the
    worker's own result, so its reason is read off its status rather than
    inherited from ours (``lost``)."""
    theirs = (stored or {}).get("content") or {}
    if not theirs or "settle_reason" in theirs:
        return {**ours, **theirs}
    return {
        **ours,
        **theirs,
        "settle_reason": str(settle_reason_for_envelope(theirs.get("status"))),
    }


async def _close_lost_async_child(
    repo: AgentWorkforceRepository,
    task: dict[str, Any],
    run: dict[str, Any] | None,
) -> _Reaped:
    """Close a background child whose worker is gone — and TELL the parent.

    Before fh4 the reaper emitted ``subagent_done`` but never filed a
    ``subagent_result``: the card said failed and the parent model never
    heard, and the issue was never woken (recon 1c/1d.3).

    Order: deliver (under the task's dedupe key) → CAS the row → event → idle.
    Delivering first is what lets the reaper CONVERGE: if the worker filed its
    result and died before ``subagent_done`` (recon 1d.2), the dedupe arm hands
    back the worker's row, and the event and the row are closed to match THAT
    status — the card and the model agree on whatever reached the model first.

    ``run_id`` is always set here: "assigned + no run" was requeued above
    (children in flight at the deploy that introduced the run↔task link have
    an unlinked run and are requeued, re-run once).
    """
    task_id = str(task["id"])
    payload = task.get("payload") or {}
    run_id = (run or {}).get("id")
    reason = settle_reason_for_lost_run(run)
    content = {
        "child_run_id": run_id,
        "subagent_type": payload.get("subagent_type"),
        "description": payload.get("description"),
        "status": "failed",
        "settle_reason": str(reason),
        "summary": _LOST_SUMMARY[reason],
        # Same fallback the worker's crash branch uses when no envelope carries
        # the cost: the child row's display column, 0.0 (logged) with no row.
        "cost_cents": await _child_row_cost_cents(run_id, UUID(task_id)),
        "byok_cents": 0,
        "tokens_used": 0,
    }
    delivery = await deliver_subagent_result(
        inbox_repo=get_agent_run_inbox_repository(),
        task_id=task_id,
        payload=payload,
        content=content,
    )
    filed = _filed_content(content, delivery.stored)
    status = str(filed.get("status") or "failed")
    lifecycle = _EVENT_STATUS_TO_LIFECYCLE.get(status, "failed")
    written = await repo.update_task_status(
        task_id=UUID(task_id),
        lifecycle_status=lifecycle,
        **(
            {}
            if lifecycle != "failed"
            else {
                "error_code": WORKER_LOST,
                "error_message": _lost_message(task, run)[:500],
            }
        ),
        only_from=_ACTIVE_PHASES,
    )
    if not written:
        # Finished between the scan and now: its own path emitted the event,
        # and a second one would contradict it. Our delivery (if any) was
        # deduped onto the worker's row, so nothing extra reached the model.
        return "raced", None
    logger.warning(
        f"[stale-tasks] task {task_id} → {lifecycle} ({reason}); "
        f"{_lost_message(task, run)}"
    )
    await emit_async_subagent_done(
        parent_run_id=str(payload.get("parent_run_id")),
        task_id=task_id,
        child_run_id=filed.get("child_run_id") or run_id,
        subagent_type=payload.get("subagent_type"),
        status=status,
        settle_reason=SettleReason(filed.get("settle_reason") or reason),
        summary=str(filed.get("summary") or ""),
        cost_cents=filed.get("cost_cents"),
        byok_cents=filed.get("byok_cents") or 0,
        tokens_used=filed.get("tokens_used") or 0,
        duration_ms=None,
    )
    await _idle_worker_if_ours(repo, task)
    order = (
        {"task_id": task_id, "idle_dispatch": delivery.idle_dispatch}
        if delivery.idle_dispatch
        else None
    )
    return ("done" if lifecycle == "done" else "failed"), order


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
