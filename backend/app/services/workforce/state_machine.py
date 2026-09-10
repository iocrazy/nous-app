"""Worker state machine.

Encapsulates the 6 worker states and the legal transitions between them.
Every transition is recorded in ``agent_state_history`` for audit.

States (mirrors the DB CHECK on agent_workers.state):
    idle, working, waiting_for_other, blocked, paused, terminated

Allowed transitions — read as ``from -> to``:

    idle           -> working               trigger: task_assigned
    working        -> idle                  trigger: task_completed
    working        -> waiting_for_other     trigger: delegated_subtask
    waiting_for_other -> working            trigger: subtask_completed
    *              -> blocked               trigger: error
    blocked        -> idle                  trigger: blocker_resolved
    *              -> paused                trigger: admin_pause
    paused         -> idle                  trigger: admin_resume
    *              -> terminated            trigger: heartbeat_lost | shutdown

Transitions outside this set raise ``InvalidTransitionError``.

## Why no DB-side lock

Earlier revisions used a Postgres advisory lock per agent (TODO-AI-012)
to prevent concurrent transitions across processes. We dropped it for
two reasons:

1. The PostgREST advisory-lock RPC is per-session — Supabase pools
   sessions across requests, so the lock can be released by an unrelated
   request before the holding request finishes. Lock acquisition would
   succeed but provide no real exclusion.
2. Dispatch is serialised per agent by the DBOS queue's
   ``queue_partition_key`` (``agent_workforce``, keyed on agent_id), and the
   task claim itself is a PG row CAS (``claim_task``). Cross-process
   exclusion for STATE transitions isn't a concern at the current deployment
   scale. (Before 2b-2 T3 this sentence credited the deleted
   ``AgentWorkerPool``'s per-agent ``asyncio.Lock`` — which was per-process,
   so it never gave what it claimed.)

If we ever scale to multiple FastAPI workers, the right fix is to move
exclusion to a real distributed primitive (Redis / Postgres
``SELECT … FOR UPDATE``) — not back to the broken PostgREST RPC.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple
from uuid import UUID

from loguru import logger

from app.repositories.agent_workforce_repository import (
    AgentWorkforceRepository,
    get_agent_workforce_repository,
)

# ─── states & transitions ────────────────────────────────────────────

WorkerState = str
Trigger = str

ALL_STATES: FrozenSet[WorkerState] = frozenset(
    {
        "idle",
        "working",
        "waiting_for_other",
        "blocked",
        "paused",
        "terminated",
    }
)

# (from_state, trigger) -> to_state. ``from_state == None`` means "any state".
ALLOWED_TRANSITIONS: dict[Tuple[Optional[WorkerState], Trigger], WorkerState] = {
    ("idle", "task_assigned"): "working",
    ("working", "task_completed"): "idle",
    ("working", "delegated_subtask"): "waiting_for_other",
    ("waiting_for_other", "subtask_completed"): "working",
    ("blocked", "blocker_resolved"): "idle",
    ("paused", "admin_resume"): "idle",
    # Wildcards (any from-state)
    (None, "error"): "blocked",
    (None, "admin_pause"): "paused",
    (None, "heartbeat_lost"): "terminated",
    (None, "shutdown"): "terminated",
}


# ─── exceptions ──────────────────────────────────────────────────────


class InvalidTransitionError(Exception):
    """Raised when a transition is not in ALLOWED_TRANSITIONS."""

    def __init__(
        self,
        *,
        from_state: Optional[WorkerState],
        to_state: WorkerState,
        trigger: Trigger,
    ) -> None:
        super().__init__(
            f"Illegal transition: {from_state!r} --({trigger})--> {to_state!r}"
        )
        self.from_state = from_state
        self.to_state = to_state
        self.trigger = trigger


# ─── helpers ─────────────────────────────────────────────────────────


def _resolve_target(
    *, from_state: Optional[WorkerState], trigger: Trigger
) -> WorkerState:
    """Look up the target state for (from_state, trigger), trying both the
    explicit-from form and the wildcard form."""
    if (from_state, trigger) in ALLOWED_TRANSITIONS:
        return ALLOWED_TRANSITIONS[(from_state, trigger)]
    if (None, trigger) in ALLOWED_TRANSITIONS:
        return ALLOWED_TRANSITIONS[(None, trigger)]
    raise InvalidTransitionError(
        from_state=from_state, to_state="<no-target>", trigger=trigger
    )


# ─── state machine ───────────────────────────────────────────────────


@dataclass(frozen=True)
class TransitionResult:
    agent_id: UUID
    from_state: Optional[WorkerState]
    to_state: WorkerState
    trigger: Trigger


class WorkerStateMachine:
    """Drives transitions for one agent at a time.

    Lock-free: in-process serialisation is provided by the pool's
    per-agent ``asyncio.Lock``. Each transition reads current state,
    validates the move, persists the new state + history row.
    """

    def __init__(
        self,
        repo: Optional[AgentWorkforceRepository] = None,
    ) -> None:
        self.repo = repo or get_agent_workforce_repository()

    async def transition(
        self,
        *,
        agent_id: UUID,
        trigger: Trigger,
        task_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
        # When current_task_id is provided, agent_workers.current_task_id is
        # also updated (used on task_assigned / task_completed).
        current_task_id: Optional[UUID] = None,
    ) -> TransitionResult:
        """Read current state, validate, persist new state + history row.
        Raises InvalidTransitionError on illegal moves."""
        worker = await self.repo.get_worker(agent_id)
        if not worker:
            # First-touch: register an idle worker so transitions resolve
            # against a known from_state.
            await self.repo.upsert_worker(agent_id=agent_id, state="idle")
            from_state: Optional[WorkerState] = "idle"
        else:
            from_state = worker.get("state")
        to_state = _resolve_target(from_state=from_state, trigger=trigger)

        await self.repo.update_worker_state(
            agent_id=agent_id,
            state=to_state,
            current_task_id=current_task_id,
        )
        await self.repo.log_state_transition(
            agent_id=agent_id,
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            task_id=task_id,
            metadata=metadata,
        )
        logger.info(
            f"[state-machine] {agent_id} {from_state} --({trigger})--> {to_state}"
        )
        return TransitionResult(
            agent_id=agent_id,
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
        )

    async def force_terminate(
        self,
        *,
        agent_id: UUID,
        reason: str = "heartbeat_lost",
        metadata: Optional[dict] = None,
    ) -> TransitionResult:
        """Sweeper-only path: force state → terminated, log history.

        Used when a worker is presumed dead (no heartbeat). Skips the
        normal transition validation since a stuck worker may be in any
        state and we just need to free its slot.
        """
        repo = self.repo
        worker = await repo.get_worker(agent_id)
        from_state: Optional[WorkerState] = worker.get("state") if worker else None

        # AI-016: a presumed-dead worker may still hold an in-flight task. If
        # we just terminate the worker, that task is orphaned in
        # assigned/in_progress forever. Requeue it so it gets picked up again.
        # ``requeue_task``'s CAS guard (phase IN ('assigned','in_progress')) is
        # the race fence: if the task already completed between the heartbeat
        # check and now, the requeue is a no-op and we don't resurrect it.
        orphan_task_id = worker.get("current_task_id") if worker else None
        if orphan_task_id:
            try:
                requeued = await repo.requeue_task(UUID(str(orphan_task_id)))
                if requeued:
                    logger.warning(
                        "[state-machine] requeued orphan task %s from "
                        "force-terminated worker %s",
                        orphan_task_id,
                        agent_id,
                    )
            except Exception:
                logger.exception(
                    "[state-machine] failed to requeue orphan task %s for %s",
                    orphan_task_id,
                    agent_id,
                )

        if not worker:
            await repo.upsert_worker(agent_id=agent_id, state="terminated")
            from_state = None
        else:
            await repo.update_worker_state(
                agent_id=agent_id, state="terminated", bump_heartbeat=False
            )
        await repo.log_state_transition(
            agent_id=agent_id,
            from_state=from_state,
            to_state="terminated",
            trigger=reason,
            metadata=metadata,
        )
        logger.warning(
            f"[state-machine] FORCE-TERMINATE {agent_id} "
            f"from={from_state} reason={reason}"
        )
        return TransitionResult(
            agent_id=agent_id,
            from_state=from_state,
            to_state="terminated",
            trigger=reason,
        )
