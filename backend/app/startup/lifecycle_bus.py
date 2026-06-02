"""Lifecycle Bus Redis listener (A8).

Subscribe to `mediahub:lifecycle` channel so this process receives events
emitted by other processes (gateway ↔ worker, multi-replica workers).
Local subscribers fire on cross-process events the same way they fire on
local emits.

See `app/services/lifecycle_bus.py`.
"""

from loguru import logger


def _on_user_concurrency(evt) -> None:
    """Apply a `config.parse_concurrency` event to every per-user batch queue.

    Fired (via the lifecycle bus, possibly cross-process from the gateway) when
    a user saves Settings → General. Payload: ``{"value": int}`` (see
    `user_settings_router.update_user_settings`). One knob caps parse + soda +
    generic download alike, so no batch-download path can exceed the per-user
    limit. Whichever process runs each queue's poller picks up the new cap
    without a restart.
    """
    value = (evt.payload or {}).get("value")
    if value is None:
        return
    try:
        from app.workflows.parse import set_parse_concurrency
        from app.workflows.soda_download import set_soda_concurrency
        from app.workflows.download import set_download_concurrency

        set_parse_concurrency(int(value))
        set_soda_concurrency(int(value))
        set_download_concurrency(int(value))
    except Exception as exc:
        logger.warning(f"[lifecycle] user_concurrency apply failed: {exc}")


async def start_lifecycle_bus() -> None:
    try:
        from app.services.lifecycle_bus import get_bus

        bus = get_bus()
        bus.subscribe(
            "config.parse_concurrency",
            _on_user_concurrency,
            name="user_concurrency_applier",
        )
        await bus.start_redis_listener()
        logger.info("Lifecycle bus redis listener started")
    except Exception as lb_exc:
        logger.warning(f"Lifecycle bus listener failed to start: {lb_exc}")


async def stop_lifecycle_bus() -> None:
    try:
        from app.services.lifecycle_bus import get_bus

        await get_bus().stop_redis_listener(timeout=2.0)
    except Exception as lb_exc:
        logger.warning(f"Lifecycle bus shutdown raised: {lb_exc}")
