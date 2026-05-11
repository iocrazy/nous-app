"""agent_workforce DBOS workflow + queue — port of the in-process
``AgentWorkerPool`` dispatch path (``app/services/workforce``).

The legacy pool was a paperclip-style asyncio scheduler: per-agent
``asyncio.Lock`` for in-process serialization, ``asyncio.create_task``
for fire-and-forget dispatch, no cross-process coordination. It was
already a step up from M2 Celery beat (per the scheduler.py docstring),
but every uvicorn worker has its own pool — so two pods could run the
same agent at once; only the PG row CAS in
``AgentWorkforceRepository.claim_next_queued`` prevented duplicate
work, not concurrent runs.

DBOS Queue gives us:
    - **cluster-wide** concurrency cap (`concurrency=N`)
    - **partitioned per-agent serialization** (`partition_queue=True`
      keyed on `agent_id`) — DBOS only runs one workflow per partition
      at a time, GLOBALLY. Stronger than the in-process lock the legacy
      pool had.
    - durable retry / replay via workflow_id memoization
    - workflow status events feed the existing D4 SSE endpoint for
      free, so a workforce dispatch is observable from any frontend
      that already speaks DBOS workflow status

Concurrency tuning rationale:
    - `concurrency=8`: matches the M3 default capacity. Bump via
      `WORKFORCE_QUEUE_CONCURRENCY` env override if memory headroom
      allows. Each in-flight workflow holds an LLM connection +
      AgentRunner stack — typical RSS is ~80MB/turn.
    - `worker_concurrency` left None: per-worker cap = global cap
      since we expect one DBOS worker per uvicorn pod for now.
"""

from __future__ import annotations

import os
from typing import Any

from dbos import DBOS, Queue

_DEFAULT_CONCURRENCY = int(os.environ.get("WORKFORCE_QUEUE_CONCURRENCY", "8"))


# Queue declaration runs at import time so the DBOS registry has it
# before init_dbos() launches. partition_queue=True means callers must
# pass a queue_partition_key when enqueueing — agent_id is the natural
# partition.
agent_workforce_queue = Queue(
    "agent_workforce",
    concurrency=_DEFAULT_CONCURRENCY,
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
    """DBOS port of the AgentWorkerPool dispatch.

    Recommended workflow_id: ``f"workforce-{task['id']}"`` so a
    duplicate enqueue (broker hiccup, scheduler tick collision)
    short-circuits to the cached result instead of re-executing.

    Recommended queue_partition_key: ``task['agent_id']`` so two
    enqueues for the same agent serialise globally — replaces the
    in-process per-agent lock from AgentWorkerPool with a stronger
    cluster-wide guarantee.

    Returns the same shape as run_one_task:
        {"task_id": str, "status": str, "run_id": str|None}"""
    return await run_one_task_step(task)
