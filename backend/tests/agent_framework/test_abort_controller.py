"""AbortController — per-run cancel signal that interrupts in-flight
LLM calls (not just hooks between turns).

Mirrors OpenClaw gateway/chat-abort.ts: an AbortController per run,
fired by external watcher (e.g., DB-polling task watching
ai_sessions.cancel_requested = True), observed by the LLM call wrapper
to short-circuit out of the await.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_framework.abort_controller import (
    AbortController,
    RunAborted,
    race_until_abort,
)


@pytest.mark.unit
async def test_initial_state_not_aborted():
    ctl = AbortController()
    assert ctl.is_aborted() is False


@pytest.mark.unit
async def test_fire_sets_aborted():
    ctl = AbortController()
    ctl.fire()
    assert ctl.is_aborted() is True


@pytest.mark.unit
async def test_fire_with_reason():
    ctl = AbortController()
    ctl.fire(reason="user clicked cancel")
    assert ctl.reason == "user clicked cancel"


@pytest.mark.unit
async def test_wait_until_aborted_returns_when_fired():
    ctl = AbortController()

    async def fire_after_delay():
        await asyncio.sleep(0.01)
        ctl.fire()

    asyncio.create_task(fire_after_delay())
    # wait_until_aborted() should return promptly after fire
    await asyncio.wait_for(ctl.wait_until_aborted(), timeout=1.0)
    assert ctl.is_aborted()


@pytest.mark.unit
async def test_race_returns_task_result_when_task_wins():
    """race_until_abort returns the task's result if abort doesn't fire."""
    ctl = AbortController()

    async def quick_task():
        await asyncio.sleep(0.01)
        return "task done"

    result = await race_until_abort(quick_task(), ctl)
    assert result == "task done"


@pytest.mark.unit
async def test_race_raises_run_aborted_when_abort_wins():
    """race_until_abort raises RunAborted if abort fires before task done."""
    ctl = AbortController()

    async def slow_task():
        await asyncio.sleep(10.0)
        return "should not return"

    async def fire_soon():
        await asyncio.sleep(0.01)
        ctl.fire(reason="user cancel")

    fire_soon_task = asyncio.create_task(fire_soon())
    try:
        with pytest.raises(RunAborted) as exc_info:
            await race_until_abort(slow_task(), ctl)
        assert "user cancel" in str(exc_info.value)
    finally:
        fire_soon_task.cancel()


@pytest.mark.unit
async def test_race_cancels_underlying_task_on_abort():
    """When abort wins, the underlying task is cancelled (not orphaned)."""
    ctl = AbortController()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def long_task():
        started.set()
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            finished.set()
            raise

    async def fire_soon():
        await started.wait()
        ctl.fire()

    fire_soon_task = asyncio.create_task(fire_soon())
    try:
        with pytest.raises(RunAborted):
            await race_until_abort(long_task(), ctl)
        # Wait briefly for cancellation propagation
        await asyncio.wait_for(finished.wait(), timeout=1.0)
        assert finished.is_set()
    finally:
        fire_soon_task.cancel()


@pytest.mark.unit
async def test_already_aborted_short_circuits():
    """If abort fired BEFORE race_until_abort starts, raise immediately."""
    ctl = AbortController()
    ctl.fire(reason="pre-fired")

    async def task():
        return "should not run to completion"

    with pytest.raises(RunAborted):
        await race_until_abort(task(), ctl)
