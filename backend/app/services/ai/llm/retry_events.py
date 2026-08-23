"""Turning a retry into a durable transcript event.

The retry middleware emits a plain dict and knows nothing about storage
(`LLMRetryMiddleware.on_retry`). This module is the one place that binds those
emissions to a run's transcript, so the middleware stays testable without a
database and a telemetry outage can never fail a model call.

Event type is ``llm_retry`` (migration 436). Not ``error`` — a retry is a
lifecycle event, and filing it as an error would light up every
"did this run fail?" query on a retry that ultimately succeeded.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

RETRY_EVENT_TYPE = "llm_retry"


def make_retry_observer(recorder: Any) -> Callable[[dict], Awaitable[None]]:
    """An ``on_retry`` observer that files each retry on ``recorder``.

    Tolerates a recorder without ``record_event`` (several call sites pass
    lightweight stand-ins) by doing nothing — a missing sink is not worth an
    exception on a path that is already handling a failure.
    """

    async def _observe(event: dict) -> None:
        record = getattr(recorder, "record_event", None)
        if record is None:
            return
        try:
            await record(RETRY_EVENT_TYPE, event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[retry_events] could not record retry: {exc!r}")

    return _observe


__all__ = ["RETRY_EVENT_TYPE", "make_retry_observer"]
