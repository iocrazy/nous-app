"""LaneQueue — per-lane bounded concurrency with task timeout.

A flood of low-priority background work (thumbnail generation,
analytics aggregation, AI summary chains) can starve high-priority
work the user is waiting on (parse on click). Without lanes the only
control is a global concurrency cap, which couples the two.

LaneQueue gives each named lane its own asyncio.Semaphore: USER work
runs in parallel up to its cap, BACKGROUND work runs in parallel up
to ITS cap, etc. They don't compete with each other for slots.

Mirrors OpenClaw ``process/command-queue.ts`` (526 lines) +
``process/lanes.ts`` (7 lines). Python translation comes out smaller
because asyncio.Semaphore handles most of what JS had to hand-code.

mediahub lanes:
  USER         user clicked something — high priority
  BACKGROUND   AI workflow chains
  SCHEDULED    cron jobs (D11) — keep at 1 to avoid overlap
  SUBAGENT     AgentRunner internal delegate

Usage:
    from app.agent_framework import LaneQueue, Lane

    queue = LaneQueue(  # one per process; held on app.state
        max_concurrent={
            Lane.USER: 5,
            Lane.BACKGROUND: 3,
            Lane.SCHEDULED: 1,
            Lane.SUBAGENT: 2,
        },
        task_timeout_ms={Lane.SCHEDULED: 5 * 60_000},
    )
    result = await queue.submit(Lane.USER, my_coroutine())
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Optional


class Lane(str, Enum):
    """Standard mediahub lanes. Stringified so logs / snapshot
    keys stay human-readable."""

    USER = "user"
    BACKGROUND = "background"
    SCHEDULED = "scheduled"
    SUBAGENT = "subagent"


# Defaults — caller can override via constructor
_DEFAULT_MAX_CONCURRENT: dict[Lane, int] = {
    Lane.USER: 5,
    Lane.BACKGROUND: 3,
    Lane.SCHEDULED: 1,
    Lane.SUBAGENT: 2,
}
_FALLBACK_MAX_CONCURRENT: int = 4  # for any lane the caller didn't override


class LaneTaskTimeout(Exception):
    """Task exceeded the lane's configured timeout. Underlying coroutine
    is cancelled (not orphaned)."""


@dataclass
class _LaneState:
    semaphore: asyncio.Semaphore
    max_concurrent: int
    in_flight: int = 0


class LaneQueue:
    """Per-lane bounded concurrency. One instance per process.

    Not thread-safe; submit must be called from the asyncio loop.
    """

    def __init__(
        self,
        *,
        max_concurrent: Optional[dict[Lane, int]] = None,
        task_timeout_ms: Optional[dict[Lane, int]] = None,
    ) -> None:
        self._lanes: dict[Lane, _LaneState] = {}
        self._task_timeout_ms: dict[Lane, int] = task_timeout_ms or {}

        # Initialize all standard lanes so snapshot() shows them
        # even if no work has hit them yet.
        for lane in Lane:
            cap = (max_concurrent or {}).get(
                lane, _DEFAULT_MAX_CONCURRENT.get(lane, _FALLBACK_MAX_CONCURRENT)
            )
            self._lanes[lane] = _LaneState(
                semaphore=asyncio.Semaphore(cap),
                max_concurrent=cap,
            )

    async def submit(self, lane: Lane, awaitable: Awaitable[Any]) -> Any:
        """Submit ``awaitable`` to ``lane``, return its result.

        Blocks until a slot is free in this lane, then runs the awaitable.
        Other lanes are unaffected — they have their own semaphores.

        Raises:
            LaneTaskTimeout: if task exceeds lane's configured timeout
            (any other exception): propagated from the awaitable
        """
        state = self._lanes[lane]
        timeout_ms = self._task_timeout_ms.get(lane)

        async with state.semaphore:
            state.in_flight += 1
            try:
                if timeout_ms is None:
                    return await awaitable
                try:
                    return await asyncio.wait_for(
                        awaitable, timeout=timeout_ms / 1000.0
                    )
                except asyncio.TimeoutError as e:
                    raise LaneTaskTimeout(
                        f"task on lane {lane.value!r} exceeded " f"{timeout_ms}ms"
                    ) from e
            finally:
                state.in_flight -= 1

    def snapshot(self) -> dict[Lane, dict[str, int]]:
        """Return per-lane state for admin UI / monitoring.

        ``{Lane.USER: {"in_flight": 2, "max_concurrent": 5}, ...}``
        """
        return {
            lane: {
                "in_flight": state.in_flight,
                "max_concurrent": state.max_concurrent,
            }
            for lane, state in self._lanes.items()
        }

    async def drain(self, lane: Lane) -> None:
        """Wait until every in-flight task on ``lane`` completes.

        Polls in_flight counter every 50ms. Doesn't reject new submits
        during drain — caller is responsible for stopping the source
        of new work if they want a hard quiesce.
        """
        state = self._lanes[lane]
        while state.in_flight > 0:
            await asyncio.sleep(0.05)
