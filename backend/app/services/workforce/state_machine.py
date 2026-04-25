"""Worker state machine.

Encapsulates the 6 worker states and the legal transitions between them.
Every transition is gated by a per-agent Postgres advisory lock (so two
processes can't drive the same agent concurrently) and recorded in
``agent_state_history`` for audit.

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

Advisory lock: each agent's UUID hashes to a stable bigint key. Lock is
acquired through the ``public.try_advisory_lock`` wrapper added in
migration 149 (PostgREST can't call pg_catalog.pg_try_advisory_lock
directly). Lock is released in a finally block. If the connection drops
mid-task, Postgres releases the lock at session end.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from typing import Any, AsyncIterator, FrozenSet, Optional, Tuple
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_workforce_repository import AgentWorkforceRepository

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


class LockNotAcquiredError(Exception):
    """Raised when ``pg_try_advisory_lock`` returns false within the budget.

    The state machine keeps this contended path failing fast rather than
    queuing internally — the dispatcher can retry on its own loop, and
    we'd rather see contention in metrics than hide it under blocking
    waits."""


# ─── helpers ─────────────────────────────────────────────────────────


def agent_lock_key(agent_id: UUID) -> int:
    """Hash a UUID into a stable, deterministic int8 advisory-lock key.

    Postgres ``pg_advisory_lock(bigint)`` takes a single signed-bigint key.
    We take the first 8 hex chars of SHA-1(uuid_bytes), mask to 63 bits, and
    return as a positive int. Same UUID always → same lock key, no collisions
    in practice given the 2^63 namespace.
    """
    digest = hashlib.sha1(agent_id.bytes).digest()
    # First 8 bytes → unsigned 64-bit int → mask off sign bit for positive int8.
    raw = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return raw & 0x7FFFFFFFFFFFFFFF


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

    The state machine is stateless across calls — each transition reads the
    current state under an advisory lock, validates the move, persists the
    new state + history row, releases the lock. Concurrent callers either
    serialise through the lock or raise ``LockNotAcquiredError``.
    """

    LOCK_FN = "try_advisory_lock"
    UNLOCK_FN = "advisory_unlock"

    def __init__(
        self,
        repo: Optional[AgentWorkforceRepository] = None,
    ) -> None:
        self.repo = repo or AgentWorkforceRepository()

    # ----- lock primitives -----

    @contextlib.asynccontextmanager
    async def _hold_lock(self, agent_id: UUID) -> AsyncIterator[Any]:
        client = await get_async_supabase_admin()
        key = agent_lock_key(agent_id)
        acquired = False
        try:
            acquired = await self._try_lock(client, key)
            if not acquired:
                raise LockNotAcquiredError(
                    f"agent={agent_id} lock held by another worker"
                )
            yield client
        finally:
            if acquired:
                try:
                    await client.rpc(self.UNLOCK_FN, {"lock_key": key}).execute()
                except Exception as err:  # pragma: no cover — defensive
                    logger.warning(
                        f"[state-machine] advisory_unlock failed (agent={agent_id}): {err}"
                    )

    async def _try_lock(self, client: Any, key: int) -> bool:
        try:
            result = await client.rpc(self.LOCK_FN, {"lock_key": key}).execute()
            return bool(result.data) if result.data is not None else False
        except Exception as err:
            logger.warning(
                f"[state-machine] try_advisory_lock failed (key={key}): {err}"
            )
            return False

    # ----- public API -----

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
        """Atomically read current state, validate, persist new state +
        history row. Raises InvalidTransitionError on illegal moves and
        LockNotAcquiredError when contended."""
        async with self._hold_lock(agent_id):
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
        """Sweeper-only path: skip the lock (the worker is presumed dead, so
        nothing else is holding it), force state → terminated, log history.

        We don't take the advisory lock here because the only reason we'd
        force-terminate is that the worker is unresponsive — taking the
        lock would either succeed because no one is holding it, or block
        if a stuck process is gripping it. Either way, the sweeper needs
        to make progress.
        """
        repo = self.repo
        worker = await repo.get_worker(agent_id)
        from_state: Optional[WorkerState] = worker.get("state") if worker else None
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
