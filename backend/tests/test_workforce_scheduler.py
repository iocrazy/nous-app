"""Unit tests for WorkforceScheduler — paperclip-style in-process loop.

Pins the contract:
- start() / stop() lifecycle works without errors
- Inbox tick fires at configured cadence (every Nth fast tick)
- Outbox tick fires every fast tick
- A failing tick doesn't break the loop
- stop() drains the worker pool
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.workforce.scheduler import WorkforceScheduler

# ─── lifecycle ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_then_stop_clean_lifecycle():
    """start → tick fires → stop drains within timeout."""
    runner = AsyncMock(return_value={"status": "done"})
    sched = WorkforceScheduler(
        runner=runner, fast_tick_seconds=0.05, inbox_every_n_ticks=1
    )

    # Inbox + outbox tick both no-op for this test.
    sched.inbox.tick = AsyncMock(return_value={})
    sched.outbox.tick = AsyncMock(return_value={})

    sched.start()
    # Let the loop spin a few times.
    await asyncio.sleep(0.18)
    await sched.stop(drain_timeout=1.0)

    # Outbox runs every tick → should have fired ~3 times in 0.18s @ 0.05s.
    assert sched.outbox.tick.await_count >= 2
    # Inbox runs every fast tick (n=1) → same count or close.
    assert sched.inbox.tick.await_count >= 2


# ─── cadence: inbox runs every Nth tick ──────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_cadence_n_equals_2():
    """With inbox_every_n_ticks=2, inbox should run half as often as outbox."""
    runner = AsyncMock()
    sched = WorkforceScheduler(
        runner=runner, fast_tick_seconds=0.03, inbox_every_n_ticks=2
    )
    sched.inbox.tick = AsyncMock(return_value={})
    sched.outbox.tick = AsyncMock(return_value={})

    sched.start()
    await asyncio.sleep(0.2)  # ~6-7 ticks
    await sched.stop(drain_timeout=1.0)

    # Inbox runs ~half as often as outbox (with at least 1 inbox call).
    assert sched.inbox.tick.await_count >= 1
    assert sched.outbox.tick.await_count >= sched.inbox.tick.await_count


# ─── failure isolation: bad tick doesn't kill the loop ───────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bad_tick_does_not_break_loop():
    """Outbox raises on every call → loop logs and keeps running."""
    runner = AsyncMock()
    sched = WorkforceScheduler(
        runner=runner, fast_tick_seconds=0.03, inbox_every_n_ticks=1
    )

    raise_count = 0

    async def bad_outbox():
        nonlocal raise_count
        raise_count += 1
        raise RuntimeError("outbox boom")

    sched.outbox.tick = bad_outbox
    sched.inbox.tick = AsyncMock(return_value={})

    sched.start()
    await asyncio.sleep(0.15)
    await sched.stop(drain_timeout=1.0)

    # Loop should have called outbox multiple times despite the exceptions.
    assert raise_count >= 2
    # Inbox kept running too.
    assert sched.inbox.tick.await_count >= 2


# ─── stop is idempotent ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_can_be_called_twice():
    runner = AsyncMock()
    sched = WorkforceScheduler(runner=runner, fast_tick_seconds=0.05)
    sched.inbox.tick = AsyncMock(return_value={})
    sched.outbox.tick = AsyncMock(return_value={})

    sched.start()
    await asyncio.sleep(0.05)
    await sched.stop(drain_timeout=1.0)
    # Second stop should be a no-op, not raise.
    await sched.stop(drain_timeout=0.5)


# ─── stop without start is safe ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    runner = AsyncMock()
    sched = WorkforceScheduler(runner=runner)
    # Should not raise — _task is None, nothing to wait on.
    await sched.stop(drain_timeout=0.5)


# ─── pool drain on stop ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_drains_worker_pool():
    """In-flight pool tasks should be drained when scheduler stops."""
    proceed = asyncio.Event()
    runs: list[str] = []

    async def slow_runner(task):
        await proceed.wait()
        runs.append(task["id"])
        return {}

    sched = WorkforceScheduler(runner=slow_runner, fast_tick_seconds=0.05)
    sched.inbox.tick = AsyncMock(return_value={})
    sched.outbox.tick = AsyncMock(return_value={})
    sched.start()

    # Manually dispatch a task on the pool to simulate inflight work.
    from uuid import uuid4

    task = {"id": str(uuid4()), "agent_id": str(uuid4()), "payload": {}}
    await sched.pool.dispatch(task)
    await asyncio.sleep(0.05)
    assert sched.pool.inflight_count == 1

    # Release runner just before stop so drain succeeds within timeout.
    proceed.set()
    await sched.stop(drain_timeout=1.0)

    assert runs == [task["id"]]
    assert sched.pool.inflight_count == 0


# ─── stop respects drain timeout (cancels stuck workers) ────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_cancels_workers_past_drain_timeout():
    cancelled = asyncio.Event()

    async def stuck_runner(task):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}

    sched = WorkforceScheduler(runner=stuck_runner, fast_tick_seconds=0.05)
    sched.inbox.tick = AsyncMock(return_value={})
    sched.outbox.tick = AsyncMock(return_value={})
    sched.start()

    from uuid import uuid4

    await sched.pool.dispatch(
        {"id": str(uuid4()), "agent_id": str(uuid4()), "payload": {}}
    )
    await asyncio.sleep(0.05)

    await sched.stop(drain_timeout=0.1)
    assert cancelled.is_set()
