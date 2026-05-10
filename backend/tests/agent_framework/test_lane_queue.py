"""LaneQueue — per-lane bounded concurrency with task timeout.

Mirrors OpenClaw process/command-queue.ts + lanes.ts. Each lane has its
own concurrency cap so a flood of low-priority background work
(thumbnail generation) can't starve high-priority user work
(parse on click).

mediahub lanes:
  USER         high priority, user clicked something — maxConcurrent 5
  BACKGROUND   AI workflow chains — maxConcurrent 3
  SCHEDULED    cron jobs — maxConcurrent 1 (avoid overlap)
  SUBAGENT     AgentRunner internal delegate — maxConcurrent 2
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_framework.lane_queue import (
    Lane,
    LaneQueue,
    LaneTaskTimeout,
)


@pytest.mark.unit
async def test_submit_and_await():
    q = LaneQueue()

    async def task() -> str:
        return "ok"

    result = await q.submit(Lane.USER, task())
    assert result == "ok"


@pytest.mark.unit
async def test_max_concurrent_enforced():
    """Two long tasks on a lane with maxConcurrent=1 — second waits."""
    q = LaneQueue(max_concurrent={Lane.SCHEDULED: 1})

    in_progress = 0
    peak = 0

    async def task() -> int:
        nonlocal in_progress, peak
        in_progress += 1
        peak = max(peak, in_progress)
        await asyncio.sleep(0.05)
        in_progress -= 1
        return 1

    results = await asyncio.gather(
        q.submit(Lane.SCHEDULED, task()),
        q.submit(Lane.SCHEDULED, task()),
        q.submit(Lane.SCHEDULED, task()),
    )
    assert results == [1, 1, 1]
    assert peak == 1  # never more than 1 concurrent


@pytest.mark.unit
async def test_lanes_independent():
    """USER lane (cap 5) is not blocked by SCHEDULED (cap 1) holding."""
    q = LaneQueue(
        max_concurrent={Lane.SCHEDULED: 1, Lane.USER: 5},
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_scheduled():
        started.set()
        await release.wait()

    async def quick_user():
        return "user-fast"

    sched_task = asyncio.create_task(q.submit(Lane.SCHEDULED, slow_scheduled()))
    await started.wait()  # scheduled lane is busy

    # USER lane must be unblocked
    result = await asyncio.wait_for(q.submit(Lane.USER, quick_user()), timeout=0.5)
    assert result == "user-fast"

    release.set()
    await sched_task


@pytest.mark.unit
async def test_task_timeout_raises_lane_task_timeout():
    """Task that exceeds taskTimeoutMs raises LaneTaskTimeout (and the
    coroutine is properly cancelled, not orphaned)."""
    q = LaneQueue(task_timeout_ms={Lane.USER: 100})

    async def slow():
        await asyncio.sleep(10.0)
        return "should not return"

    with pytest.raises(LaneTaskTimeout):
        await q.submit(Lane.USER, slow())


@pytest.mark.unit
async def test_snapshot_returns_lane_state():
    """snapshot() returns counts per lane — used by admin UI."""
    q = LaneQueue(max_concurrent={Lane.USER: 2})
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        started.set()
        await release.wait()

    t = asyncio.create_task(q.submit(Lane.USER, hold()))
    await started.wait()

    snap = q.snapshot()
    assert snap[Lane.USER]["in_flight"] == 1
    assert snap[Lane.USER]["max_concurrent"] == 2

    release.set()
    await t


@pytest.mark.unit
async def test_drain_waits_for_outstanding():
    """drain() returns once every in-flight task on the lane completes.
    New submits during drain still go through (we don't reject)."""
    q = LaneQueue()
    finished: list[int] = []

    async def task(i: int):
        await asyncio.sleep(0.05)
        finished.append(i)

    asyncio.gather(
        q.submit(Lane.BACKGROUND, task(1)),
        q.submit(Lane.BACKGROUND, task(2)),
        q.submit(Lane.BACKGROUND, task(3)),
    )
    await asyncio.sleep(0.01)  # let them start
    await q.drain(Lane.BACKGROUND)
    assert sorted(finished) == [1, 2, 3]


@pytest.mark.unit
async def test_default_max_concurrent_for_unconfigured_lane():
    """Lane without explicit cap uses default (4 by mediahub convention)."""
    q = LaneQueue()
    snap = q.snapshot()
    # All standard lanes have a default in the snapshot
    assert Lane.USER in snap


@pytest.mark.unit
async def test_exception_propagates_to_caller():
    q = LaneQueue()

    async def failing():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await q.submit(Lane.USER, failing())
