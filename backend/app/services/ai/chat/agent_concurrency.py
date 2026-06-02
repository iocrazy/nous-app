"""In-process per-user concurrency gate for agent turns (chat + issue).

A user gets at most ``current_limit()`` concurrent turns; the rest await a slot.
Per-process (chat runs on the gateway, issue on the worker) — sufficient for the
single-gateway deployment. A Redis token would make it global across replicas.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

_DEFAULT = int(os.environ.get("MAX_AGENT_CONCURRENCY_PER_USER", "2"))
_limit = max(1, min(20, _DEFAULT))
_sems: dict[str, asyncio.Semaphore] = {}
_counts: dict[str, int] = {}


def current_limit() -> int:
    return _limit


def set_agent_concurrency(n: int) -> None:
    """Set the per-user agent cap live (clamped 1..20). New semaphores use the
    new limit; any in-flight ones drain at their old limit (acceptable)."""
    global _limit, _sems
    try:
        _limit = max(1, min(20, int(n)))
    except (TypeError, ValueError):
        return
    _sems = {}  # rebuild lazily at the new limit


def _sem_for(user_id: str) -> asyncio.Semaphore:
    sem = _sems.get(user_id)
    if sem is None:
        sem = asyncio.Semaphore(_limit)
        _sems[user_id] = sem
    return sem


@asynccontextmanager
async def user_slot(user_id: str):
    """Hold one of the user's agent-turn slots for the duration of the block."""
    sem = _sem_for(user_id)
    await sem.acquire()
    _counts[user_id] = _counts.get(user_id, 0) + 1
    try:
        yield
    finally:
        sem.release()
        _counts[user_id] = max(0, _counts.get(user_id, 0) - 1)


def reset_for_tests() -> None:
    global _sems, _counts
    _sems = {}
    _counts = {}
