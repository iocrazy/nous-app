"""lane_queue — 4-lane priority isolation for task dispatch.

Why this exists
---------------
Today every dispatched task hits the same DBOS queue (concurrency=8).
That means:
  * 8 slow ai_visual_analysis runs can saturate the pool, blocking
    a user's just-clicked parse from starting for minutes.
  * A flood of background sweeper-spawned tasks competes head-to-head
    with high-priority user clicks.
  * No way for ops to throttle a misbehaving lane (e.g., an analysis
    backlog) without affecting unrelated work.

Lane queue solves it by carving the executor capacity into 4 fixed
slices, each with its own asyncio.Semaphore:

  * `User`      — direct user clicks (parse / download / fetch).
                  Highest priority, max 5 concurrent.
  * `Background` — chained AI workflows (transcribe / summary /
                   visual_analysis). Max 3.
  * `Scheduled` — cron + master scheduler triggers. Max 1 (one tick
                  doesn't pile on top of the previous).
  * `Subagent`  — agent-delegated subtasks (workforce inbox/outbox).
                  Max 2.

Borrowed from openclaw `process/command-queue.ts:526` + `process/
lanes.ts:7`. The borrowed model: per-lane bounded concurrency with
explicit `acquire` / `release` semantics + a snapshot endpoint for
ops dashboards.

Process-local (each worker process has its own semaphores). For
cluster-wide caps, DBOS queue concurrency still applies on top of
this — lane queue limits per-process slot usage; DBOS limits across
the cluster.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

from loguru import logger

# ── Lane definitions ────────────────────────────────────────────

# Defaults can be overridden per-lane via env. Keep the totals modest —
# cumulative max = 5 + 3 + 1 + 2 = 11 across all lanes; that's the per-
# process ceiling. Multi-replica deployments scale by N.
_LANE_DEFAULTS = {
    "user": int(os.environ.get("LANE_QUEUE_USER", "5")),
    "background": int(os.environ.get("LANE_QUEUE_BACKGROUND", "3")),
    "scheduled": int(os.environ.get("LANE_QUEUE_SCHEDULED", "1")),
    "subagent": int(os.environ.get("LANE_QUEUE_SUBAGENT", "2")),
}

# Map task_type → lane. Tasks without a mapping land in "background"
# (safe default — slower than user but not blocking the high-prio
# lane). Extend as new task_types appear.
_TASK_TYPE_LANE: Dict[str, str] = {
    # User-clicked
    "parse": "user",
    "download": "user",
    "fetch": "user",
    "upload": "user",
    # Background AI chain
    "transcode": "background",
    "thumbnail": "background",
    "ai_extract": "background",
    "ai_transcription": "background",
    "ai_summary": "background",
    "ai_visual_analysis": "background",
    "script_outline_gen": "background",
    # Scheduled / cron
    "scheduled_master": "scheduled",
    "agent_runs_sweeper": "scheduled",
    "stuck_task_reaper": "scheduled",
    "system_status": "scheduled",
    # Subagent / agent workforce
    "agent_workforce": "subagent",
    "agent_inbox": "subagent",
    "agent_outbox": "subagent",
}


@dataclass
class _LaneState:
    name: str
    capacity: int
    sem: asyncio.Semaphore
    in_flight: int = 0
    queued: int = 0
    total_acquired: int = 0
    last_wait_ms: float = 0.0


class LaneQueue:
    """Per-process 4-lane bounded concurrency.

    Use as an async context manager via `acquire(lane)`:

        async with lane_queue.acquire("user"):
            await do_work()

    The wait happens inside `acquire`; `do_work` runs only after a
    slot is available.
    """

    def __init__(self, lane_capacities: Optional[Dict[str, int]] = None) -> None:
        caps = lane_capacities or _LANE_DEFAULTS
        self._lanes: Dict[str, _LaneState] = {
            name: _LaneState(
                name=name,
                capacity=cap,
                sem=asyncio.Semaphore(cap),
            )
            for name, cap in caps.items()
        }

    @asynccontextmanager
    async def acquire(self, lane: str) -> AsyncIterator[None]:
        """Acquire a slot in `lane`. Blocks until one is free.

        Unknown lane → falls back to "background" (logged at debug).
        """
        state = self._lanes.get(lane)
        if state is None:
            logger.debug(f"[lane_queue] unknown lane {lane!r}; routing to background")
            state = self._lanes["background"]

        state.queued += 1
        import time

        t0 = time.time()
        try:
            await state.sem.acquire()
        finally:
            state.queued -= 1

        state.in_flight += 1
        state.total_acquired += 1
        state.last_wait_ms = (time.time() - t0) * 1000
        try:
            yield
        finally:
            state.in_flight -= 1
            state.sem.release()

    def lane_for_task(self, task_type: str) -> str:
        """Return the lane name a task_type routes to."""
        return _TASK_TYPE_LANE.get(task_type, "background")

    def snapshot(self) -> List[Dict[str, Any]]:
        """Per-lane status for the admin /lanes/snapshot endpoint."""
        return [
            {
                "name": s.name,
                "capacity": s.capacity,
                "in_flight": s.in_flight,
                "queued": s.queued,
                "total_acquired": s.total_acquired,
                "last_wait_ms": round(s.last_wait_ms, 2),
                "saturation_pct": round(
                    (s.in_flight / s.capacity * 100) if s.capacity else 0, 1
                ),
            }
            for s in self._lanes.values()
        ]


# ── Singleton ────────────────────────────────────────────────────

_queue: Optional[LaneQueue] = None


def get_lane_queue() -> LaneQueue:
    global _queue
    if _queue is None:
        _queue = LaneQueue()
    return _queue


__all__ = [
    "LaneQueue",
    "get_lane_queue",
]
