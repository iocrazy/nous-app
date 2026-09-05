"""The ONE best-effort way to put an event on a run's transcript.

Every emitter (todo snapshots, compaction bracket, turn_end, step brackets,
inbox claims, budget checks …) goes through ``emit``. Before this module
there were three near-identical private helpers; a source guard in
``tests/runner/test_events_guard.py`` keeps a fourth from growing.

Telemetry never fails a turn: ``None`` recorder → silence; a recorder
without ``record_event`` → silence; a recorder that raises → one warning.
``turn`` / ``step`` are the dsh Location coordinates (mig 453 columns);
recorders that predate them (test stand-ins) still work through the
positional fallback.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger


async def emit(
    recorder: Any,
    event_type: str,
    payload: dict[str, Any],
    *,
    turn: Optional[int] = None,
    step: Optional[int] = None,
) -> bool:
    """Return True when the recorder accepted the event, False otherwise."""
    if recorder is None:
        return False
    record = getattr(recorder, "record_event", None)
    if record is None:
        return False
    try:
        try:
            await record(event_type, payload, turn=turn, step=step)
        except TypeError:
            # Stand-ins with the pre-453 two-argument signature.
            await record(event_type, payload)
        return True
    except Exception as exc:  # noqa: BLE001 — telemetry never fails a turn
        logger.warning("[events] {} not recorded: {}", event_type, exc)
        return False


__all__ = ["emit"]
