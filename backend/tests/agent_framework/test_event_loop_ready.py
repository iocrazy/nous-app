"""event_loop_ready — drift detection for cold-start.

DBOS init + connection pools + workflow registration take a few seconds
to settle. If we mark the backend "healthy" too early, traffic hits a
loaded loop and the first requests time out for no real reason.

This module probes asyncio.sleep(0) drift: scheduled-vs-actual delay.
When drift falls below threshold (default 200ms) for N consecutive
checks (default 2), the loop is "ready".

Mirrors OpenClaw gateway/event-loop-ready.ts.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agent_framework.event_loop_ready import (
    measure_drift_ms,
    wait_for_loop_ready,
)


@pytest.mark.unit
async def test_measure_drift_returns_small_when_loop_idle():
    """An idle loop has drift close to 0."""
    drift = await measure_drift_ms()
    assert drift >= 0.0
    assert drift < 50.0  # generous — CI machines vary


@pytest.mark.unit
async def test_wait_for_loop_ready_returns_when_idle():
    """Healthy loop returns ready promptly."""
    start = time.time()
    ok = await wait_for_loop_ready(
        threshold_ms=200, consecutive_passes=2, max_wait_seconds=2.0
    )
    elapsed = time.time() - start
    assert ok is True
    assert elapsed < 2.0  # didn't time out


@pytest.mark.unit
async def test_wait_for_loop_ready_times_out_under_load():
    """When the loop is loaded, wait returns False within max_wait."""
    # Spin a tight CPU-bound task that NEVER yields cleanly enough
    busy = asyncio.Event()
    stop = asyncio.Event()

    async def hog():
        busy.set()
        # Tight loop without async sleep — but still has to yield
        # to the asyncio loop occasionally. Use repeated short sleeps
        # WHICH DO NOT YIELD long enough to settle drift.
        while not stop.is_set():
            # Block via time.sleep (blocks the event loop entirely)
            time.sleep(0.05)
            await asyncio.sleep(0)

    hog_task = asyncio.create_task(hog())
    await busy.wait()
    try:
        # threshold very small, max_wait short — should fail
        ok = await wait_for_loop_ready(
            threshold_ms=1, consecutive_passes=3, max_wait_seconds=0.5
        )
        assert ok is False
    finally:
        stop.set()
        await hog_task


@pytest.mark.unit
async def test_wait_for_loop_ready_requires_consecutive_passes():
    """One good measurement isn't enough — need consecutive_passes
    consecutive readings below threshold (defends against transient
    quiet moments during a noisy startup)."""
    ok = await wait_for_loop_ready(
        threshold_ms=200, consecutive_passes=5, max_wait_seconds=3.0
    )
    assert ok is True
