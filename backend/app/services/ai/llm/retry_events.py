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

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.services.ai.runner.events import emit

RETRY_EVENT_TYPE = "llm_retry"


def make_retry_observer(recorder: Any) -> Callable[[dict], Awaitable[None]]:
    """An ``on_retry`` observer that files each retry on ``recorder``.

    Stamps ``at`` (UTC ISO) so the Task Center can age the wait; the fold
    copies it — folds never read the clock, replay must be deterministic.
    Goes through the single event entry (``runner/events.py``), which
    tolerates a recorder without ``record_event`` and never raises.
    """

    async def _observe(event: dict) -> None:
        await emit(
            recorder,
            RETRY_EVENT_TYPE,
            {**event, "at": datetime.now(timezone.utc).isoformat()},
        )

    return _observe


__all__ = ["RETRY_EVENT_TYPE", "make_retry_observer"]
