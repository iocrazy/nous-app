"""Lifecycle Bus Redis listener (A8).

Subscribe to `mediahub:lifecycle` channel so this process receives events
emitted by other processes (gateway ↔ worker, multi-replica workers).
Local subscribers fire on cross-process events the same way they fire on
local emits.

See `app/services/lifecycle_bus.py`.
"""

from loguru import logger


async def start_lifecycle_bus() -> None:
    try:
        from app.services.lifecycle_bus import get_bus

        await get_bus().start_redis_listener()
        logger.info("Lifecycle bus redis listener started")
    except Exception as lb_exc:
        logger.warning(f"Lifecycle bus listener failed to start: {lb_exc}")


async def stop_lifecycle_bus() -> None:
    try:
        from app.services.lifecycle_bus import get_bus

        await get_bus().stop_redis_listener(timeout=2.0)
    except Exception as lb_exc:
        logger.warning(f"Lifecycle bus shutdown raised: {lb_exc}")
