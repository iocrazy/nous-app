"""AgentWorkerPool — in-process asyncio scheduler for persistent agents.

Replaces the M2 Celery-beat workforce dispatch with a paperclip-style
single-process model: one ``asyncio.Task`` per FastAPI worker process,
each agent gets an ``asyncio.Lock`` so the same agent never runs two
turns concurrently inside this process. Different agents run in
parallel on the same event loop.

## Three layers of concurrency protection

1. **PG row-level CAS** in ``AgentWorkforceRepository.claim_next_queued``
   filters on ``lifecycle_status='queued'``, so two processes racing to
   claim the same task can't both win.
2. **Per-agent ``asyncio.Lock``** in this pool serialises in-process
   turns for the same ``agent_id``. Cross-agent runs stay parallel.
3. **PG RLS + ``user_id`` filtering** isolates user data — no inter-user
   leakage is structurally possible.

## Why agent-level locking (not per-user-per-agent)

Default semantics: agent is a server-side singleton. User A and User B
asking ``summarize`` to do work share the same singleton — second call
waits ~5 seconds for first to finish. This matches paperclip / OpenAI
Operator / Replit Agent. To switch to per-user instances later, change
the lock key from ``agent_id`` to ``(agent_id, user_id)`` — schema
already carries ``user_id`` everywhere.

## Lifecycle

The pool is created in ``app.main`` lifespan and held on
``app.state.worker_pool``. ``shutdown()`` cancels all in-flight tasks
gracefully (with a 5s drain budget) so uvicorn restart doesn't strand
workers mid-LLM-call.

## Failure isolation

Each agent's worker coroutine catches its own exceptions and logs them.
A buggy agent never poisons the rest of the pool. Tasks that crash mid-
run are flipped to ``lifecycle_status='failed'`` with ``error_*`` fields
populated.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

logger = logging.getLogger(__name__)


# Type alias for the per-task runner callable. Injected so tests can
# stub it without standing up the full AgentRunner stack.
TaskRunner = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# Default drain timeout when shutting down the pool. After this, any
# in-flight worker is cancelled outright. Five seconds is enough for a
# turn to finish a tool call but short enough that uvicorn doesn't hang.
SHUTDOWN_DRAIN_SECONDS = 5.0


class AgentWorkerPool:
    """In-process pool dispatching tasks to per-agent workers.

    Construction is intentionally cheap so tests can spin up a fresh
    pool per test. The expensive part is the injected ``runner``
    callable, which is built once in main.py lifespan with all the
    M1+M2 wiring (HookRegistry, fallback chain, RunRecorder factory).
    """

    def __init__(self, runner: TaskRunner) -> None:
        self._runner = runner
        # Per-agent lock — created lazily on first dispatch. Holding the
        # lock during a turn means a second dispatch for the same agent
        # blocks here, not in the DB. Cross-agent dispatches never share
        # a lock so they run truly in parallel.
        self._locks: dict[UUID, asyncio.Lock] = {}
        # Track in-flight tasks so shutdown() can drain them.
        self._inflight: set[asyncio.Task[Any]] = set()
        self._closed = False

    # ────────────────────────────────────────────────────────────
    # Dispatch
    # ────────────────────────────────────────────────────────────

    async def dispatch(self, task: dict[str, Any]) -> None:
        """Schedule a task on the pool. Returns immediately; the actual
        run happens asynchronously.

        The caller (inbox processor) hands off and continues. The pool's
        ``_run_one`` coroutine acquires the per-agent lock, calls the
        injected runner, and finalises lifecycle.

        Idempotent on closed pool — silently drops dispatches after
        shutdown to avoid stranding shutting-down requests.
        """
        if self._closed:
            logger.warning(
                f"[worker-pool] dispatch on closed pool, dropping task {task.get('id')}"
            )
            return

        agent_id_raw = task.get("agent_id")
        if not agent_id_raw:
            logger.error(f"[worker-pool] task missing agent_id: {task}")
            return
        agent_id = UUID(agent_id_raw) if isinstance(agent_id_raw, str) else agent_id_raw

        # Schedule the run as a background task so the caller can move
        # on. We hold a strong reference until completion so GC doesn't
        # reap it mid-await.
        coro = self._run_one(agent_id, task)
        asyncio_task = asyncio.create_task(
            coro, name=f"worker:{agent_id}:{task.get('id')}"
        )
        self._inflight.add(asyncio_task)
        # Drop the reference once done — preserves _inflight as a snapshot
        # of *currently* running tasks for shutdown drain.
        asyncio_task.add_done_callback(self._inflight.discard)

    async def _run_one(self, agent_id: UUID, task: dict[str, Any]) -> None:
        """Acquire per-agent lock, invoke runner, swallow exceptions.

        Errors are logged here. The runner itself is responsible for
        flipping the task to ``failed`` status — this layer only makes
        sure a bug in the runner doesn't crash the whole pool.

        Once a task is dispatched, it always gets a chance to run —
        ``shutdown()`` only blocks NEW dispatches. Tasks queued on the
        per-agent lock at shutdown time still run, on the assumption
        that finishing them is cheaper than rolling them back. If the
        drain budget expires, ``shutdown()`` cancels them outright via
        ``asyncio.Task.cancel()``.
        """
        lock = self._locks.setdefault(agent_id, asyncio.Lock())
        try:
            async with lock:
                try:
                    await self._runner(task)
                except asyncio.CancelledError:
                    # Shutdown asked us to stop. Re-raise so the create_task
                    # is marked cancelled cleanly.
                    raise
                except Exception as err:
                    logger.exception(
                        f"[worker-pool] runner crashed for "
                        f"agent={agent_id} task={task.get('id')}: {err}"
                    )
        except asyncio.CancelledError:
            logger.info(
                f"[worker-pool] cancelled mid-turn for agent={agent_id} "
                f"task={task.get('id')}"
            )

    # ────────────────────────────────────────────────────────────
    # Introspection
    # ────────────────────────────────────────────────────────────

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    @property
    def known_agents(self) -> set[UUID]:
        """Agents that have acquired a lock at least once. Stable across
        the pool's lifetime; tests use this to verify lock keying."""
        return set(self._locks.keys())

    # ────────────────────────────────────────────────────────────
    # Shutdown
    # ────────────────────────────────────────────────────────────

    async def shutdown(self, drain_timeout: float = SHUTDOWN_DRAIN_SECONDS) -> None:
        """Stop accepting new dispatches; wait up to ``drain_timeout`` for
        in-flight runs to finish. Hard-cancel anything still running."""
        if self._closed:
            return
        self._closed = True

        if not self._inflight:
            return

        logger.info(
            f"[worker-pool] shutdown — draining {len(self._inflight)} "
            f"in-flight task(s), timeout={drain_timeout}s"
        )

        # Snapshot so iteration is safe even as tasks complete and remove
        # themselves from the set.
        pending = list(self._inflight)
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=drain_timeout,
            )
            logger.info("[worker-pool] drain complete")
        except asyncio.TimeoutError:
            still_running = [t for t in pending if not t.done()]
            logger.warning(
                f"[worker-pool] drain timeout; cancelling "
                f"{len(still_running)} in-flight task(s)"
            )
            for t in still_running:
                t.cancel()
            # Wait briefly for cancellation to land.
            await asyncio.gather(*still_running, return_exceptions=True)

        # Explicit clear: the per-task done_callback (set.discard) may
        # not have fired yet at this point — asyncio runs callbacks on
        # the next loop tick. Without this, callers reading
        # ``inflight_count`` immediately after ``shutdown()`` could see
        # stale entries even though every task is done.
        self._inflight.clear()
