"""Event-loop-ready probe (D10-6).

Short bounded wait so the first request doesn't hit a still-loaded loop.
Capped at 2s — anything longer suggests a sweeper is hogging the loop,
which is its own bug to fix.
"""

from loguru import logger


async def probe_event_loop_ready() -> None:
    try:
        from app.agent_framework import wait_for_loop_ready

        ready = await wait_for_loop_ready(
            threshold_ms=200,
            consecutive_passes=2,
            max_wait_seconds=2.0,
        )
        if ready:
            logger.info("Event loop ready (drift settled)")
        else:
            logger.info(
                "Event loop did not settle within 2s — accepting traffic anyway"
            )
    except Exception as e:
        logger.warning(f"Event-loop-ready probe failed: {e}")
