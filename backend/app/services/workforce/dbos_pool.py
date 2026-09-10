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
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from dbos import SetEnqueueOptions, SetWorkflowID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchRecord:
    """What actually went onto the queue, handed back so the caller can persist
    it verbatim.

    Both fields have to be stored: ``attempt`` is what the NEXT dispatch
    increments, and ``workflow_id`` is the ownership token ``claim_task``
    compares against when a replay walks back into its own run. Recomputing
    either at the call site would risk storing an id different from the one
    enqueued, which silently breaks the re-entry check."""

    workflow_id: str
    attempt: int


def workflow_id_for(task_id: str, attempt: int) -> str:
    """The single place the id is spelled. ``workforce-<task_id>-<attempt>``.

    ⚠️ The attempt suffix is load-bearing, not cosmetic. Re-enqueuing an id
    DBOS already holds does not re-run the workflow: ``insert_workflow_status``
    upserts with ``set_={recovery_attempts, updated_at}`` only, leaves the row
    at its terminal status, and ``init_workflow`` answers
    ``should_execute=False`` (``dbos/_sys_db.py``). DBOS "dedup" means NEVER
    AGAIN. A task that legitimately needs another run — one that came back
    through ``requeue_task`` after its worker died — therefore has to arrive
    under an id DBOS has never seen, or it is enqueued forever and executed
    never while the dispatch loop happily counts it as sent."""
    return f"workforce-{task_id}-{int(attempt)}"


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

    async def dispatch(self, task: dict[str, Any]) -> Optional[DispatchRecord]:
        """Enqueue the task as a DBOS workflow on the per-agent partition.
        Returns immediately.

        Returns the ``DispatchRecord`` that was enqueued, or ``None`` when
        nothing was — a closed pool, a malformed row, or a failed enqueue.
        ``None`` means the caller must NOT stamp the row as dispatched: a stamp
        without an enqueue hides the task from the next tick's work list."""
        if self._closed:
            logger.warning(
                f"[dbos-workforce-pool] dispatch on closed pool, "
                f"dropping task {task.get('id')}"
            )
            return None

        agent_id_raw = task.get("agent_id")
        task_id_raw = task.get("id")
        if not agent_id_raw or not task_id_raw:
            logger.error(f"[dbos-workforce-pool] task missing agent_id or id: {task}")
            return None

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

        attempt = int(task.get("dispatch_attempt") or 0) + 1
        workflow_id = workflow_id_for(str(task_id_raw), attempt)
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
            return DispatchRecord(workflow_id=workflow_id, attempt=attempt)
        except Exception as err:
            # NOT a duplicate-id handler: DBOS does not raise for a workflow id
            # it already holds, it silently declines to execute (see
            # ``workflow_id_for``). Anything that lands here is a real enqueue
            # failure — no sys_db connection, queue not registered, unencodable
            # input. Report it as one and let the caller leave the row
            # unstamped so the next tick retries.
            logger.exception(
                f"[dbos-workforce-pool] enqueue failed for task={task_id_raw}: {err}"
            )
            return None

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
