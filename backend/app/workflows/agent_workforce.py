"""agent_workforce DBOS workflow + queue — the execution path for agent tasks.

It began as a port of the in-process ``AgentWorkerPool``
(``app/services/workforce/worker_pool.py``), a paperclip-style asyncio
scheduler: per-agent ``asyncio.Lock`` for in-process serialization,
``asyncio.create_task`` for fire-and-forget dispatch, no cross-process
coordination. Every uvicorn worker had its own pool, so two pods could run the
same agent at once. That module and its scheduler were deleted in harness
2b-2 T3 — this is the only path now, and the comparisons below are history,
not a choice still on the table.

DBOS Queue gives us:
    - **per-worker** concurrency cap (`worker_concurrency=N`, see note
      below on why not global `concurrency`)
    - **partitioned per-agent dispatch** (`partition_queue=True` keyed
      on `agent_id`) — the per-worker cap is enforced PER agent partition.
    - durable retry / replay via workflow_id memoization
    - workflow status events feed the existing D4 SSE endpoint for
      free, so a workforce dispatch is observable from any frontend
      that already speaks DBOS workflow status

Concurrency tuning rationale:
    - `worker_concurrency=8` (NOT global `concurrency`): matches the M3
      default capacity. Bump via `WORKFORCE_QUEUE_CONCURRENCY` env
      override if memory headroom allows. Each in-flight workflow holds
      an LLM connection + AgentRunner stack — typical RSS ~80MB/turn.
    - **Why `worker_concurrency` not global `concurrency`:** DBOS uses
      `FOR UPDATE NOWAIT` + REPEATABLE READ when a global `concurrency`
      is set, which under load raises LockNotAvailable and backs the
      poll interval off to a 120s cap (queue stalls). `worker_concurrency`
      uses `FOR UPDATE SKIP LOCKED` + READ COMMITTED — no contention
      backoff. (2026-06-03 pipeline-immediacy fix.)
    - **Multi-worker caveat:** the cap is now PER WORKER, so on an
      N-worker deploy the effective per-agent ceiling is 8×N and the
      queue no longer coordinates per-agent serialization cluster-wide.
      Today we run one DBOS worker per pod, so per-worker == global.
      Duplicate-run prevention does NOT rely on the queue regardless —
      it is the PG row-level CAS in
      ``AgentWorkforceRepository.claim_task`` / ``update_task_status``
      (a second worker that dequeues the same agent loses the claim UPDATE
      in ``run_one_task`` and returns ``status='skipped'``).
"""

from __future__ import annotations

import os
from typing import Any

from dbos import DBOS, Queue
from loguru import logger

_DEFAULT_CONCURRENCY = int(os.environ.get("WORKFORCE_QUEUE_CONCURRENCY", "8"))


# Queue declaration runs at import time so the DBOS registry has it
# before init_dbos() launches. partition_queue=True means callers must
# pass a queue_partition_key when enqueueing — agent_id is the natural
# partition.
agent_workforce_queue = Queue(
    "agent_workforce",
    worker_concurrency=_DEFAULT_CONCURRENCY,
    partition_queue=True,
)


# PR-D8 Phase 2 — per-run subprocess isolation
#
# Two execution modes:
#   AGENT_RUN_ISOLATION=inprocess   (default; legacy behavior)
#       Runs the task inline in the worker process. Fastest, but a leak
#       or crash inside `run_one_task` accumulates / wedges the worker.
#
#   AGENT_RUN_ISOLATION=subprocess  (new; per-run isolation)
#       Spawns a fresh `python -m app.run_isolated` for each task,
#       applies RLIMIT_AS + wall-clock timeout, exits when done. OS
#       reclaims all memory / FDs / threads. ~1-2s cold-start tax per
#       task — fine for AI runs that take 10s+ end-to-end.
#
# Tunables (only consulted in subprocess mode):
#   AGENT_RUN_MEM_LIMIT_MB  — RLIMIT_AS cap (POSIX). Default 1024 (1 GiB).
#   AGENT_RUN_TIMEOUT_S     — wall-clock cap. Default 600 (10 min).
_ISOLATION_MODE = os.environ.get("AGENT_RUN_ISOLATION", "inprocess").lower()
_RUN_MEM_LIMIT_MB = int(os.environ.get("AGENT_RUN_MEM_LIMIT_MB", "1024"))
_RUN_TIMEOUT_S = float(os.environ.get("AGENT_RUN_TIMEOUT_S", "600"))


@DBOS.step()
async def run_one_task_step(task: dict[str, Any]) -> dict[str, Any]:
    """Async wrapper that runs the existing async ``run_one_task``.

    All task lifecycle bookkeeping (queued → assigned → in_progress →
    done/failed) happens inside ``run_one_task`` via
    ``AgentWorkforceRepository.update_task_status``. We don't duplicate
    that here — DBOS workflow status is a separate, parallel signal
    consumed by the frontend SSE endpoint.

    Idempotent on replay: ``run_one_task`` early-returns when the task
    isn't in 'queued'/'assigned' state any more, so re-execution after
    a worker crash short-circuits cleanly.

    See module-level docstring for ``AGENT_RUN_ISOLATION`` modes.

    Why ``async def`` (PR #237 audit): formerly used ``asyncio.run()``
    which broke under asyncpg's loop-bound pool — see
    workflow_health_sweeper.py / agent_runs_sweeper.py headers."""
    if _ISOLATION_MODE == "subprocess":
        # Phase 2 path: fresh process per run, full crash isolation.
        from app.services.workforce.isolated_runner import run_isolated

        result = run_isolated(
            task,
            timeout_s=_RUN_TIMEOUT_S,
            mem_limit_mb=_RUN_MEM_LIMIT_MB,
        )
        return result.as_worker_dict()

    # Legacy in-process path — kept as default until subprocess mode
    # graduates from staging.
    from app.services.workforce.agent_worker import run_one_task

    return await run_one_task(task)


@DBOS.workflow()
async def agent_workforce_workflow(task: dict[str, Any]) -> dict[str, Any]:
    """Run one agent task under DBOS.

    Required workflow_id: ``f"workforce-{task['id']}-{attempt}"`` (see
    ``dbos_pool.workflow_id_for``) so a duplicate enqueue of the SAME attempt
    short-circuits instead of re-executing, while a task that legitimately
    needs another run arrives under an id DBOS has never seen.
    ``DbosAgentWorkforcePool.dispatch`` sets it; nothing else enqueues here.

    Required queue_partition_key: ``task['agent_id']`` so two enqueues for the
    same agent serialise — this took over from the deleted pool's in-process
    per-agent lock, with a cluster-wide guarantee instead of a per-process one.

    Returns the same shape as run_one_task:
        {"task_id": str, "status": str, "run_id": str|None}"""
    # Thread OUR workflow id down as the claim's ownership token. It is stable
    # across a replay of this same workflow (that is what a replay means), so
    # a run that died after claiming can walk back into its own row; a
    # different worker's id will not match and cannot steal it. Put in the task
    # dict rather than a new step argument so the subprocess isolation mode
    # marshals it for free.
    result = await run_one_task_step(
        {**task, "workforce_workflow_id": DBOS.workflow_id}
    )
    await _dispatch_idle_wake(result)
    return result


async def _dispatch_idle_wake(result: dict[str, Any]) -> None:
    """Carry out the wake ORDER the step returned. Runs in the workflow BODY.

    The step cannot do this itself: ``deliver_or_dispatch``'s idle arm reaches
    ``DBOS.start_workflow``, and DBOS asserts ``cur_ctx.is_workflow()`` there.
    Before Task 7b the worker called it inline and that assertion fired on
    every background sub-agent result handed to an idle issue — masked by the
    sweeper's one-minute backstop, so the symptom read as latency rather than
    as a path that had never worked. Same split as
    ``agent_runs_sweeper._drain_one_issue`` and ``scheduled_master``.

    ``already_enqueued=True``: the worker already filed the result on the
    inbox, so this asks ONLY whether a turn should start now — the busy /
    paused / dispatching / terminal decision stays where it is owned, and the
    busy arm writes no second row (which is also why ``content`` is unused
    there and the order need not carry it into ``dbos.operation_outputs``).

    Nothing here may raise. The child has run, its result is filed, and the
    task row is closed; a wake-up lost to a blip costs the sweeper's next
    tick, whereas failing the workflow would misreport work that succeeded.
    Both failure shapes are reported — an exception AND the typed value the
    idle arm returns instead of raising."""
    order = (result or {}).get("idle_dispatch")
    if not order:
        return

    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    issue_id = int(order["issue_id"])
    try:
        outcome = await deliver_or_dispatch(
            issue_id,
            kind="subagent_result",
            content={},
            user_id=str(order["user_id"]),
            already_enqueued=True,
        )
    except Exception as err:  # noqa: BLE001 — booked, never swallowed
        logger.opt(exception=True).error(
            f"[workforce] issue {issue_id}: the sub-agent result is filed but "
            f"the wake-up raised: {err}"
        )
        return

    if outcome.mode == "skipped" and str(outcome.reason or "").startswith(
        "dispatch_failed"
    ):
        logger.error(
            f"[workforce] issue {issue_id}: the sub-agent result is filed but "
            f"the wake-up failed: {outcome.reason}"
        )
    else:
        logger.info(
            f"[workforce] issue {issue_id}: sub-agent result wake-up "
            f"{outcome.mode}/{outcome.reason or outcome.workflow_id}"
        )
