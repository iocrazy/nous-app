"""DbosAgentWorkforcePool — enqueues agent tasks onto the DBOS
`agent_workforce` queue.

This is now the ONLY pool. The in-process ``AgentWorkerPool`` it was written
to stand in for, and the ``WorkforceScheduler`` that would have chosen between
them, were deleted in phase 2b-2 T3 — nothing had imported either since PR-D8
Phase 3 moved dispatch onto DBOS-scheduled workflows. The swap point and the
``WORKFORCE_USE_DBOS_QUEUE`` switch are gone with them; do not reintroduce a
"pluggable pool" seam for a second implementation that does not exist.

Its caller is the workflow BODY of ``inbox_dispatch_workflow``
(``app/workflows/workforce_dispatch.py``) — never a ``@DBOS.step``, which
cannot start a workflow (CLAUDE.md route C).

Lifecycle semantics:
    - We don't track in-flight asyncio.Tasks here — DBOS owns that.
    - shutdown() just flips a closed flag so dispatch() rejects new
      enqueues. In-flight workflows continue under DBOS control until
      they finish or DBOS itself stops.
    - inflight_count is DERIVED from task_tracking on every call (an async
      method, not a property). It used to be a local counter incremented at
      enqueue and decremented "when DBOS reports terminal" — except nothing
      ever reported terminal, so it only rose. A gauge that cannot fall is
      worse than no gauge: it reads as a stuck queue forever.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from dbos import SetEnqueueOptions, SetWorkflowID

logger = logging.getLogger(__name__)


class DbosAgentWorkforcePool:
    """Enqueues onto DBOS instead of running work in this process.

    The ``runner`` constructor arg is accepted but NOT used: the workflow body
    is fixed (``agent_workforce_workflow`` delegates to
    ``app.services.workforce.agent_worker.run_one_task``). Tests that need to
    stub the runner should patch ``run_one_task`` directly. It survives only
    because callers construct the pool positionally; it is not a seam.
    """

    def __init__(self, runner: Any = None) -> None:
        # runner ignored — workflow body is import-bound.
        self._runner = runner
        self._closed = False
        # Track agent_ids we've ever enqueued for — an introspection aid for
        # this pool instance only, NOT a cluster-wide fact.
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
            logger.error(f"[dbos-workforce-pool] task missing agent_id or id: {task}")
            return

        agent_id = UUID(agent_id_raw) if isinstance(agent_id_raw, str) else agent_id_raw
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
            with (
                SetWorkflowID(workflow_id),
                SetEnqueueOptions(queue_partition_key=partition_key),
            ):
                # Enqueue is sync — returns a WorkflowHandle without
                # blocking on the workflow body. DBOS schedules it.
                agent_workforce_queue.enqueue(agent_workforce_workflow, task)
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

    async def inflight_count(self) -> int:
        """Live agent_tasks, derived from task_tracking — NOT counted in this
        process. Async, and a method rather than a property, because it is a
        DB read.

        The estimate this replaces only ever incremented (nothing decremented
        it on a terminal state), so it was a monotonically rising number
        wearing a gauge's name — a health consumer reading it would report a
        permanently growing backlog on a completely idle system."""
        from app.repositories.agent_workforce_repository import (
            get_agent_workforce_repository,
        )

        return await get_agent_workforce_repository().count_inflight_agent_tasks()

    @property
    def known_agents(self) -> set[UUID]:
        """Agents we've enqueued at least one task for since this pool
        was constructed."""
        return set(self._known_agents)

    async def shutdown(self, drain_timeout: float = 5.0) -> None:
        """Flip closed; DBOS owns the workflow lifecycle so we don't
        wait for in-flight runs. drain_timeout is ignored (kept so existing
        call sites need no edit).

        DBOS itself stops via ``shutdown_dbos()`` in the lifespan
        teardown, which drains its workers cleanly."""
        self._closed = True
