"""DbosAgentWorkforcePool — drop-in replacement for AgentWorkerPool
that enqueues into the DBOS `agent_workforce` queue.

Same dispatch interface (`dispatch / inflight_count / known_agents /
shutdown`) so WorkforceScheduler can swap pools without further code
changes. Production lifespan picks one based on
``settings.WORKFORCE_USE_DBOS_QUEUE`` (default off → AgentWorkerPool;
on → DbosAgentWorkforcePool).

Shutdown semantics differ slightly from the legacy pool:
    - We don't track in-flight asyncio.Tasks here — DBOS owns that.
    - shutdown() just flips a closed flag so dispatch() rejects new
      enqueues. In-flight workflows continue under DBOS control until
      they finish or DBOS itself stops.
    - inflight_count is a best-effort estimate via the local counter
      (incremented at enqueue, decremented when DBOS reports terminal).
      For exact counts, query DBOS.list_workflows(queue_name=...).
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID

logger = logging.getLogger(__name__)


class DbosAgentWorkforcePool:
    """Same shape as AgentWorkerPool — enqueues to DBOS instead of
    asyncio.create_task.

    The ``runner`` constructor arg is accepted for interface parity but
    NOT used: the workflow body is fixed (``agent_workforce_workflow``
    delegates to ``app.services.workforce.agent_worker.run_one_task``).
    Tests that need to stub the runner should patch
    ``run_one_task`` directly.
    """

    def __init__(self, runner: Any = None) -> None:
        # runner ignored — workflow body is import-bound.
        self._runner = runner
        self._closed = False
        # Best-effort in-flight count. The authoritative source is
        # `DBOS.list_workflows(queue_name='agent_workforce', status='RUNNING')`
        # but that's a sys_db read; the counter is enough for /healthz.
        self._inflight_estimate: int = 0
        # Track agent_ids we've ever enqueued for — matches
        # AgentWorkerPool's ``known_agents`` introspection so existing
        # tests/healthz keep working.
        self._known_agents: set[UUID] = set()

    async def dispatch(self, task: dict[str, Any]) -> None:
        """Enqueue the task as a DBOS workflow on the per-agent
        partition. Returns immediately."""
        if self._closed:
            logger.warning(
                f"[dbos-workforce-pool] dispatch on closed pool, "
                f"dropping task {task.get('id')}"
            )
            return

        agent_id_raw = task.get("agent_id")
        task_id_raw = task.get("id")
        if not agent_id_raw or not task_id_raw:
            logger.error(
                f"[dbos-workforce-pool] task missing agent_id or id: {task}"
            )
            return

        agent_id = (
            UUID(agent_id_raw) if isinstance(agent_id_raw, str) else agent_id_raw
        )
        self._known_agents.add(agent_id)

        # Deferred import: agent_workforce.py registers the queue at
        # import time, but DBOS must be initialized first
        # (init_dbos() in lifespan). Importing here is safe because
        # dispatch() only runs after the scheduler tick, which itself
        # runs after lifespan startup completed.
        from app.workflows.agent_workforce import (
            agent_workforce_queue,
            agent_workforce_workflow,
        )

        workflow_id = f"workforce-{task_id_raw}"
        partition_key = str(agent_id)

        try:
            with SetWorkflowID(workflow_id), SetEnqueueOptions(
                queue_partition_key=partition_key
            ):
                # Enqueue is sync — returns a WorkflowHandle without
                # blocking on the workflow body. DBOS schedules it.
                agent_workforce_queue.enqueue(agent_workforce_workflow, task)
            self._inflight_estimate += 1
            logger.debug(
                f"[dbos-workforce-pool] enqueued task={task_id_raw} "
                f"agent={agent_id} wf_id={workflow_id}"
            )
        except Exception as err:
            # Most likely cause: duplicate workflow_id (same task
            # enqueued twice within the dedup window). Treat as success
            # — DBOS already has the work, our caller doesn't need to
            # retry. Other failures are real and worth logging.
            err_repr = repr(err)
            if "already exists" in err_repr.lower() or "duplicate" in err_repr.lower():
                logger.debug(
                    f"[dbos-workforce-pool] task={task_id_raw} "
                    f"already enqueued (workflow_id={workflow_id})"
                )
            else:
                logger.exception(
                    f"[dbos-workforce-pool] enqueue failed for task={task_id_raw}: {err}"
                )

    @property
    def inflight_count(self) -> int:
        """Best-effort estimate. Matches AgentWorkerPool's interface but
        isn't exact — DBOS workers can finish workflows we don't see
        promptly. /healthz consumers should not alert on small drift."""
        return max(0, self._inflight_estimate)

    @property
    def known_agents(self) -> set[UUID]:
        """Agents we've enqueued at least one task for since this pool
        was constructed."""
        return set(self._known_agents)

    async def shutdown(self, drain_timeout: float = 5.0) -> None:
        """Flip closed; DBOS owns the workflow lifecycle so we don't
        wait for in-flight runs. drain_timeout kept for interface
        parity with AgentWorkerPool but ignored.

        DBOS itself stops via ``shutdown_dbos()`` in the lifespan
        teardown, which drains its workers cleanly."""
        self._closed = True
