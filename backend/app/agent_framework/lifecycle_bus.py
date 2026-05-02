"""LifecycleBus — in-process pub/sub for lifecycle events.

Decouples emitters from listeners. A workflow emits ``workflow.start``;
listeners (Discord notify, Realtime push, agent_runs writer, Sentry,
metrics) each subscribe to event types they care about. Without this
bus, the workflow code has to know about every listener and call them
all directly — change one listener, change the workflow.

Listener exceptions are isolated. A buggy Discord notifier crashing
inside its handler MUST NOT break the workflow that emitted the event.
This is the load-bearing contract that makes the bus safe to add new
listeners to.

Mirrors OpenClaw ``sessions/session-lifecycle-events.ts`` (28 lines that
punch above their weight).

Wildcard subscription:
    bus.subscribe("*", listener)  # receives EVERY event

Sync listeners supported (wrapped at dispatch time) — pre-existing
modules with ``def on_event(e):`` get back-compat without rewriting.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Union

from loguru import logger

WILDCARD = "*"


@dataclass(frozen=True)
class LifecycleEvent:
    """A single event flowing through the bus.

    Type is a dotted string ("workflow.start" / "workflow.complete" /
    "agent.run" / "audit.boundary_blocked" / etc.) — keep it stable
    across emitters because that's the subscription key.

    Payload is a free-form dict — listeners read fields they care about.
    Keep it JSON-serializable so Realtime / Sentry / Discord can ship
    it without per-listener serialization code.
    """

    type: str
    payload: dict[str, Any] = field(default_factory=dict)


# Listener may be sync or async; we wrap sync at dispatch time.
Listener = Union[
    Callable[[LifecycleEvent], Awaitable[None]],
    Callable[[LifecycleEvent], None],
]


class LifecycleBus:
    """In-process pub/sub. One bus instance per process — typically
    held on app.state.lifecycle_bus.

    Not thread-safe; all subscribe/emit must happen on the asyncio loop.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = {}

    def subscribe(self, event_type: str, listener: Listener) -> Callable[[], None]:
        """Subscribe ``listener`` to ``event_type``. Use ``"*"`` to
        receive every event regardless of type.

        Returns an unsubscribe handle: calling it removes this exact
        subscription (idempotent — multiple unsub calls are safe).
        """
        self._listeners.setdefault(event_type, []).append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners[event_type].remove(listener)
            except (ValueError, KeyError):
                pass  # already removed / never subscribed

        return unsubscribe

    async def emit(self, event: LifecycleEvent) -> None:
        """Dispatch ``event`` to all matching listeners. Returns when
        every listener has finished (or raised — exceptions logged not
        propagated).

        Listeners run CONCURRENTLY (asyncio.gather). A slow listener
        doesn't block fast ones.
        """
        targets: list[Listener] = []
        targets.extend(self._listeners.get(event.type, []))
        targets.extend(self._listeners.get(WILDCARD, []))

        if not targets:
            return

        async def _safe_call(listener: Listener) -> None:
            try:
                if inspect.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    # Sync listener — call directly. If it's slow,
                    # it'll block the loop, but we don't have a clean
                    # way to thread-pool it generically. Document the
                    # contract: subscribers should be async or fast.
                    listener(event)  # type: ignore[arg-type]
            except Exception as exc:
                logger.warning(
                    f"[lifecycle_bus] listener for {event.type!r} raised "
                    f"{type(exc).__name__}: {exc} — continuing"
                )

        await asyncio.gather(*(_safe_call(t) for t in targets))


# ---- Standard event-type constants ----
# Centralised so emitters and listeners agree on the spelling.

EVT_WORKFLOW_START = "workflow.start"
EVT_WORKFLOW_COMPLETE = "workflow.complete"
EVT_WORKFLOW_FAIL = "workflow.fail"
EVT_AGENT_RUN_START = "agent.run.start"
EVT_AGENT_RUN_COMPLETE = "agent.run.complete"
EVT_BOUNDARY_BLOCKED = "boundary.blocked"
EVT_DEPLOY_COMPLETE = "deploy.complete"
