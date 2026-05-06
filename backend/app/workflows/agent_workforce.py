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

import asyncio
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


@DBOS.step()
def run_one_task_step(task: dict[str, Any]) -> dict[str, Any]:
    """Sync wrapper that runs the existing async ``run_one_task``.

    All task lifecycle bookkeeping (queued → assigned → in_progress →
    done/failed) happens inside ``run_one_task`` via
    ``AgentWorkforceRepository.update_task_status``. We don't duplicate
    that here — DBOS workflow status is a separate, parallel signal
    consumed by the frontend SSE endpoint.

    Idempotent on replay: ``run_one_task`` early-returns when the task
    isn't in 'queued'/'assigned' state any more, so re-execution after
    a worker crash short-circuits cleanly."""
    from app.services.workforce.agent_worker import run_one_task

    return asyncio.run(run_one_task(task))


@DBOS.workflow()
def agent_workforce_workflow(task: dict[str, Any]) -> dict[str, Any]:
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
    return run_one_task_step(task)
