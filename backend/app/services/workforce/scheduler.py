"""WorkforceScheduler — paperclip-style in-process tick loop.

Replaces the M2 Celery beat tasks (``agent_workforce_tasks.py``) with
a single ``asyncio.Task`` owned by FastAPI's lifespan. One tick loop
calls InboxProcessor.tick() + OutboxDispatcher.tick() on intervals.

## Why not Celery any more

Celery beat fires the tick via a separate worker process; each
``asyncio.run()`` rebuilds the event loop + httpx connection pool,
adding 100-200ms of pure overhead per tick. With 10s tick cadence,
that's ~25 minutes of waste per day. Per-process advisory-lock RPCs
also failed to actually mutex (per-session limitation), so the
whole "distributed scheduler" story was illusory.

In-process is strictly better for our deployment shape: single uvicorn
worker per host, one tick loop per host, no broker hop, real shared
memory for the AgentWorkerPool's per-agent locks.

## Sub-tick cadences

Two pieces of work, two cadences:
- **Inbox** drains messages → tasks → dispatches to worker pool. 10s.
- **Outbox** flips ``delivered=true`` for outbox rows + cross-agent
  re-enqueues. 5s (user-visible Realtime path).

Both run in the same loop on a fast tick (5s) — odd ticks do outbox
only, every other tick does both. Cheaper than two coroutines + two
locks.

## Failure isolation

Each tick is wrapped in try/except. A bad tick logs and continues to
the next interval. Scheduler shutdown is cooperative — sets a stop
event, drains the worker pool with timeout.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.workforce.agent_worker import run_one_task
from app.services.workforce.inbox_processor import InboxProcessor
from app.services.workforce.outbox_dispatcher import OutboxDispatcher
from app.services.workforce.worker_pool import AgentWorkerPool

logger = logging.getLogger(__name__)


# Tick intervals — both round divisors of common observation windows.
# Fast tick = outbox cadence. Inbox runs every other fast tick.
DEFAULT_FAST_TICK_SECONDS = 5.0
DEFAULT_INBOX_EVERY_N_TICKS = 2  # → 10s inbox cadence


class WorkforceScheduler:
    """Single-process scheduler for the M2/M3 workforce dispatch loop.

    Holds the AgentWorkerPool, the inbox/outbox processors, and the
    asyncio.Task driving the loop. The pool's lifetime is bound to the
    scheduler's — start() builds it, stop() drains it.
    """

    def __init__(
        self,
        *,
        runner: Any = run_one_task,
        pool: Optional[Any] = None,
        fast_tick_seconds: float = DEFAULT_FAST_TICK_SECONDS,
        inbox_every_n_ticks: int = DEFAULT_INBOX_EVERY_N_TICKS,
    ) -> None:
        # Injected runner makes tests trivial: pass an AsyncMock and
        # observe call counts. Production binds run_one_task.
        # `pool` is also injectable so the lifespan can swap in
        # DbosAgentWorkforcePool (D5) without scheduler-internal changes.
        # When None, defaults to in-process AgentWorkerPool — original
        # M3 behaviour.
        self.pool = pool if pool is not None else AgentWorkerPool(runner=runner)
        self.inbox = InboxProcessor(dispatcher=self.pool)
        self.outbox = OutboxDispatcher()
        self.fast_tick_seconds = fast_tick_seconds
        self.inbox_every_n_ticks = max(1, inbox_every_n_ticks)
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._tick_count = 0
        self._last_tick_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._started_at: Optional[datetime] = None

    # ────────────────────────────────────────────────────────────
    # Health surface (P milestone)
    # ────────────────────────────────────────────────────────────

    @property
    def alive(self) -> bool:
        """True iff the loop coroutine is scheduled and not done."""
        return self._task is not None and not self._task.done()

    def health_snapshot(self) -> dict[str, Any]:
        """Lightweight self-report for the /workforce/healthz endpoint.

        No I/O — just the in-memory counters / timestamps. Callers
        decide whether the values mean ``healthy`` / ``degraded`` /
        ``down``.
        """
        last = self._last_tick_at
        seconds_since_last_tick: Optional[float] = None
        if last is not None:
            seconds_since_last_tick = max(
                0.0, (datetime.now(timezone.utc) - last).total_seconds()
            )
        return {
            "alive": self.alive,
            "tick_count": self._tick_count,
            "fast_tick_seconds": self.fast_tick_seconds,
            "inbox_every_n_ticks": self.inbox_every_n_ticks,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_tick_at": last.isoformat() if last else None,
            "seconds_since_last_tick": seconds_since_last_tick,
            "last_error": self._last_error,
            "pool_inflight": self.pool.inflight_count,
        }

    # ────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._loop(), name="workforce-scheduler")

    async def stop(self, drain_timeout: float = 5.0) -> None:
        """Signal stop, wait for the loop to exit, then drain the pool."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=drain_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[workforce-scheduler] loop didn't exit within {drain_timeout}s; cancelling"
                )
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
        await self.pool.shutdown(drain_timeout=drain_timeout)

    # ────────────────────────────────────────────────────────────
    # Internals
    # ────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        logger.info(
            f"[workforce-scheduler] loop start "
            f"(fast={self.fast_tick_seconds}s, inbox_every={self.inbox_every_n_ticks})"
        )
        try:
            while not self._stop_event.is_set():
                self._tick_count += 1
                try:
                    await self._tick()
                    self._last_tick_at = datetime.now(timezone.utc)
                    self._last_error = None
                except Exception as err:
                    # A failing tick must not break the loop. Record the
                    # error so /healthz can surface it without scraping
                    # logs.
                    self._last_error = f"{type(err).__name__}: {err}"[:240]
                    logger.exception(f"[workforce-scheduler] tick error: {err}")
                # Sleep with bailout on stop signal.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.fast_tick_seconds
                    )
                    # Event set → exit cleanly.
                    break
                except asyncio.TimeoutError:
                    # Normal cadence path.
                    continue
        finally:
            logger.info(f"[workforce-scheduler] loop exit (ticks={self._tick_count})")

    async def _tick(self) -> None:
        # Outbox + inbox each guarded independently — a crash in one
        # must not skip the other inside the same tick window.
        try:
            await self.outbox.tick()
        except Exception as err:
            logger.exception(f"[workforce-scheduler] outbox tick error: {err}")

        if self._tick_count % self.inbox_every_n_ticks == 0:
            try:
                await self.inbox.tick()
            except Exception as err:
                logger.exception(f"[workforce-scheduler] inbox tick error: {err}")
