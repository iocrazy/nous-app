"""event_loop_ready — drift detection for cold-start.

DBOS init + connection pools + workflow registration take a few seconds
to settle. If we mark the backend "healthy" too early, traffic hits a
loaded loop and the first requests time out for no real reason — looks
like a server bug, is actually a startup race.

This module probes ``asyncio.sleep(0)`` drift: scheduled-vs-actual
delay. When drift falls below ``threshold_ms`` for ``consecutive_passes``
consecutive checks, the loop is "ready".

Usage in app.main lifespan:

    from app.agent_framework import wait_for_loop_ready

    # ... after dbos.launch_dbos() and other slow init ...
    if await wait_for_loop_ready(threshold_ms=200, consecutive_passes=2):
        logger.success("event loop ready — accepting traffic")
    else:
        logger.warning("event loop did not settle within timeout")

Mirrors OpenClaw ``gateway/event-loop-ready.ts``.
"""

from __future__ import annotations

import asyncio
import time


async def measure_drift_ms() -> float:
    """Return the loop's current scheduling drift in milliseconds.

    Schedules ``asyncio.sleep(0)`` and measures how long it actually
    takes to come back. On an idle loop this is sub-millisecond; on a
    saturated loop it can be tens or hundreds of ms.
    """
    start = time.perf_counter()
    await asyncio.sleep(0)
    elapsed_s = time.perf_counter() - start
    return elapsed_s * 1000.0


async def wait_for_loop_ready(
    *,
    threshold_ms: float = 200.0,
    consecutive_passes: int = 2,
    max_wait_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
) -> bool:
    """Block until the loop has been quiet for ``consecutive_passes``
    consecutive measurements below ``threshold_ms``.

    Args:
        threshold_ms: Drift below this counts as "quiet".
        consecutive_passes: Need this many consecutive quiet readings
            before declaring ready (defends against transient quiet
            moments during a noisy startup).
        max_wait_seconds: Give up after this long and return False.
        poll_interval_seconds: Wait between successive measurements.

    Returns:
        True if the loop became ready within max_wait, False otherwise.
    """
    deadline = time.time() + max_wait_seconds
    passes = 0
    while time.time() < deadline:
        drift = await measure_drift_ms()
        if drift < threshold_ms:
            passes += 1
            if passes >= consecutive_passes:
                return True
        else:
            # Reset — drift spike resets the consecutive counter
            passes = 0
        await asyncio.sleep(poll_interval_seconds)
    return False
