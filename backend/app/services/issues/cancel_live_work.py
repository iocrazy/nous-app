"""Cancelling an issue stops the work on it (defects C and F, 2026-09-23).

Before this module a cancel only wrote ``issues.status``. Production S4: the
live root run never saw ``cancel_requested`` (nothing raised it for an issue
run), so its next step started 36 s after the cancel. S7b: a workflow parked on
the budget question stayed PENDING for the whole 72 h recv TTL.

``stop_live_work_for_cancel`` is called by ``IssueRepository.transition_status``
on the edge non-terminal → ``cancelled`` (``is_cancel_edge``). The repository
is the one seam every cancel goes through: the ``/transition`` endpoint, the
budget question's Cancel answer, and the workflow-instantiation cleanup of
legacy mirror issues.

Only ``cancelled``, never ``done`` (ruling 3): ``done`` is also written by
three stage-close paths (``advance_service`` / ``surface_completion`` /
``instantiation``), which would stop a live run on a mirror issue.

Each step is best-effort: a failure is logged at ERROR and the next step still
runs. The cancel itself has already been written and is never undone here.
The step the run is in finishes and is billed, as with pause (ruling 4); what
a cancel guarantees is that no next step starts.

Steps, in order:

1. Raise ``agent_runs.cancel_requested`` on the ROOT run in flight. The
   ``CancelHook`` already wired on issue runs stops it at the next step
   boundary with ``stop_reason="cancelled"``.
2. Release a workflow parked on ``await_user_input``, using the stale-wait
   reaper's and the fork's recipe: cancel the workflow, then clear the marker
   and release the execution lock. This runs in the API process, which has a
   DBOSClient but no DBOS singleton (defect H, 2026-09-23). If the cancel still
   fails, the release leaves marker and lock untouched and this step only logs
   at ERROR: the worker's minute sweeper (``reap_preempted_input_waits``)
   finishes it.
3. Disarm the wake-ups the agent armed on the issue (defect B), reason
   ``issue_terminal``. The fire path already drops them lazily; disarming
   now keeps the Schedules block and the pending-wake-ups chip truthful.
   A person's wake-up is left as is (the terminal guard stops it at fire).
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from loguru import logger

# Statuses a cancel does not "stop work" from: nothing is running on them.
# Same set as ``issue_lifecycle.PREEMPT_STATUSES``; not imported from there
# because that module pulls in DBOS and the repository calls into this one.
TERMINAL_STATUSES = frozenset({"cancelled", "done", "closed"})

# DBOS statuses of a workflow that is still waiting (and so can be released).
_WAITING_WORKFLOW_STATUSES = frozenset({"PENDING", "ENQUEUED"})


def is_cancel_edge(prev_status: str | None, new_status: str) -> bool:
    """True only for a transition from a live status into ``cancelled``. A
    repeat save (``cancelled → cancelled``, the budget answer's double-send
    window) and ``done`` do not fire."""
    return new_status == "cancelled" and prev_status not in TERMINAL_STATUSES


async def stop_live_work_for_cancel(
    issue_id: int, *, issue: dict[str, Any] | None = None
) -> None:
    """Stop whatever is still working on a just-cancelled issue.

    ``issue`` is the row as written by the transition. The repository passes it
    so the issue is not read a second time. Without it the row is loaded here.
    """
    row = issue if issue is not None else await _load_issue(issue_id)
    if row is None:
        logger.error(f"[cancel_live_work] issue {issue_id} not found; nothing stopped")
        return
    await _best_effort("flag running run", issue_id, _flag_running_root_run, row)
    await _best_effort("release parked workflow", issue_id, _release_if_parked, row)
    await _best_effort("disarm agent wake-ups", issue_id, _disarm_agent_wakeups, row)


async def _best_effort(
    name: str,
    issue_id: int,
    step: Callable[[int, dict[str, Any]], Awaitable[None]],
    row: dict[str, Any],
) -> None:
    try:
        await step(issue_id, row)
    except Exception as exc:  # noqa: BLE001 — the cancel is already written
        logger.error(f"[cancel_live_work] {name} failed for issue {issue_id}: {exc!r}")


# ── step 1: the running root run ────────────────────────────────────────────


async def _flag_running_root_run(issue_id: int, row: dict[str, Any]) -> None:
    runs = _runs_repo()
    # A failed read raises (running_root_run_id never reads failure as "no
    # run"); _best_effort logs it at ERROR.
    run_id = await runs.running_root_run_id(
        issue_id=issue_id, conversation_id=_conversation_id(row)
    )
    if run_id is None:
        return
    # No user_id: authorised at the target (the transition's caller could see
    # the issue), not by run ownership. Same rule as /pause.
    if not await runs.request_cancel(str(run_id)):
        logger.info(
            f"[cancel_live_work] issue {issue_id}: run {run_id} was no longer "
            "running when the cancel flag was raised"
        )


def _conversation_id(row: dict[str, Any]) -> int | None:
    """The issue's session conversation is the live key for its runs
    (``agent_runs.issue_id`` is backfilled after the turn)."""
    sid = row.get("ai_session_id")
    return int(sid) if sid is not None else None


# ── step 2: the parked workflow (defect F) ──────────────────────────────────


async def _release_if_parked(issue_id: int, row: dict[str, Any]) -> None:
    workflow_id = parked_workflow_id(row)
    if workflow_id is None:
        return
    try:
        status = await _workflow_status(workflow_id)
    except Exception as exc:  # noqa: BLE001 — marker + lock is enough to act
        # Leaving it would strand the workflow for the recv TTL (72 h); the
        # release is the reaper's recipe and safe on a workflow that ended.
        logger.error(
            f"[cancel_live_work] issue {issue_id}: status of {workflow_id} "
            f"unreadable ({exc!r}); releasing anyway"
        )
        status = "PENDING"
    if status not in _WAITING_WORKFLOW_STATUSES:
        logger.info(
            f"[cancel_live_work] issue {issue_id}: workflow {workflow_id} is "
            f"{status!r}, not waiting; nothing to release"
        )
        return
    try:
        await _release_parked_workflow(workflow_id)
    except Exception as exc:  # noqa: BLE001 — the marker stays; the reaper retries
        logger.error(
            f"[cancel_live_work] issue {issue_id}: release of parked workflow "
            f"{workflow_id} failed ({exc!r}); marker and lock left for the "
            "worker reaper"
        )
        return
    logger.info(
        f"[cancel_live_work] issue {issue_id}: released parked workflow {workflow_id}"
    )


def parked_workflow_id(row: dict[str, Any]) -> str | None:
    """The workflow parked on an unanswered question, or None.

    Same predicate as the fork endpoint (``issue_fork.fork_run``):
    ``execute_issue`` holds ``execution_locked_at`` while parked, and the
    ``awaiting_input`` marker has no ``answered_at`` yet. An answered marker
    means the workflow was woken and is resuming. Its loop re-checks the
    status after the wake, and a run it starts is stopped by step 1."""
    if not row.get("execution_locked_at") or not row.get("dbos_workflow_id"):
        return None
    state = row.get("execution_state") or {}
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            return None
    marker = state.get("awaiting_input") if isinstance(state, dict) else None
    if not isinstance(marker, dict) or marker.get("answered_at"):
        return None
    return str(row["dbos_workflow_id"])


# ── step 3: the agent's wake-ups (defect B) ─────────────────────────────────


async def _disarm_agent_wakeups(issue_id: int, row: dict[str, Any]) -> None:
    from app.repositories import user_schedules_repository as schedules

    n = await schedules.disarm_agent_wakeups(issue_id, reason=schedules.ISSUE_TERMINAL)
    if n:
        logger.info(
            f"[cancel_live_work] issue {issue_id}: disarmed {n} agent wake-up(s)"
        )


# ── seams (patched in tests) ────────────────────────────────────────────────


def _runs_repo() -> Any:
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    return get_agent_runs_repository()


async def _load_issue(issue_id: int) -> dict[str, Any] | None:
    from app.repositories.issue_repository import issue_repository

    return await issue_repository.get_by_id(int(issue_id))


async def _workflow_status(workflow_id: str) -> str | None:
    """The DBOS status of a workflow, or None when DBOS does not know it.

    Client-aware like ``workflows_router._status_read``: the cancel edge runs
    in the API process, where the singleton read raises
    ``DBOSException('No DBOS was created yet')``."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        from dbos._error import DBOSNonExistentWorkflowError

        try:
            handle = await client.retrieve_workflow_async(workflow_id)
            status = await handle.get_status()
        except DBOSNonExistentWorkflowError:
            return None
        return status.status
    from dbos import DBOS

    status = await DBOS.get_workflow_status_async(workflow_id)
    return status.status if status is not None else None


async def _release_parked_workflow(workflow_id: str) -> None:
    from app.agent_framework.input_gate import release_parked_workflow

    await release_parked_workflow(workflow_id)


__all__ = [
    "TERMINAL_STATUSES",
    "is_cancel_edge",
    "parked_workflow_id",
    "stop_live_work_for_cancel",
]
