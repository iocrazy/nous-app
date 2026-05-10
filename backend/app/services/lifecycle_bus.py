"""lifecycle_bus — process-local + cross-process event bus.

Why this exists
---------------
Mediahub today couples event producers to event consumers directly:

  * Workflow finishes → trigger UPDATE on task_tracking → Postgres
    Realtime broadcasts → Frontend WS handler decodes
  * Workflow fails → `_failure_handler.record_workflow_failure` is
    imported into the workflow module (cycle risk) → it manually
    writes to multiple sinks (task_tracking, agent_runs, log)
  * Subscribers (Discord notifier, Sentry, agent_runs telemetry) each
    have to be wired into every producer's code path

That's a star-shaped coupling graph that grows N×M as we add producers
and subscribers. Every new sink (e.g., a Slack notifier, a metrics
counter) requires touching every workflow.

A Lifecycle Bus inverts the dependency: producers `emit(event)` once,
subscribers `subscribe(filter, handler)` independently. New subscribers
ship without touching producer code. Producers don't need to import
subscribers. The bus is the only shared symbol.

Two transports
--------------

In-process (`_local_subscribers`):
  Direct asyncio fan-out. ~10 microsecond per subscriber. Used by
  every emit; same-process subscribers receive synchronously inside
  the emit call (so they see the event before emit returns).

Cross-process (Redis pubsub):
  Producers PUBLISH to `mediahub:lifecycle` channel. A background task
  on every process SUBSCRIBEs and re-emits received events to the
  local bus. This lets gateway processes receive events emitted by
  worker processes (or vice versa) without any direct connection.

  The background task is registered in the FastAPI lifespan via
  `start_redis_listener`. It auto-reconnects on Redis errors with
  exponential backoff capped at 30s.

Event shape
-----------
Events are immutable dataclasses with:
  * `name`: dotted topic ("workflow.completed", "task.cancelled")
  * `payload`: JSON-serializable dict (must round-trip through Redis)
  * `source`: who emitted it ("worker:host-pid" / "gateway:host-pid")
  * `ts`: epoch ms (set by emit)

Subscribers filter on name patterns ("workflow.*", "task.cancelled").
A subscriber registered with `name="*"` receives every event.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import socket
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from loguru import logger

REDIS_CHANNEL = "mediahub:lifecycle"


@dataclass(frozen=True)
class Event:
    """A single lifecycle event. Frozen so subscribers can't mutate
    the payload they share with other subscribers."""

    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    ts: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, raw: str) -> "Event":
        d = json.loads(raw)
        return cls(
            name=d.get("name", ""),
            payload=d.get("payload") or {},
            source=d.get("source") or "",
            ts=int(d.get("ts") or 0),
        )


# A subscriber is either a sync callable or an async callable; we await
# it transparently in `_dispatch_local`.
_Subscriber = Callable[[Event], Union[None, Awaitable[None]]]


@dataclass
class _Registration:
    pattern: str  # fnmatch-style ("workflow.*", "*", "task.cancelled")
    handler: _Subscriber
    name: str  # human-readable subscriber name for log


class LifecycleBus:
    """Process-local event bus + Redis pubsub bridge.

    Singleton via `get_bus()`; tests can construct fresh instances.
    """

    def __init__(self) -> None:
        self._subs: List[_Registration] = []
        self._lock = asyncio.Lock()
        self._self_source = self._compute_source()
        self._listener_task: Optional[asyncio.Task] = None

    @staticmethod
    def _compute_source() -> str:
        host = socket.gethostname()
        role = os.environ.get("MEDIAHUB_ROLE", "combined")
        return f"{role}:{host}-pid{os.getpid()}"

    # ── Subscription ────────────────────────────────────────────────

    def subscribe(
        self, pattern: str, handler: _Subscriber, *, name: str = ""
    ) -> Callable[[], None]:
        """Register a subscriber for events whose `name` matches `pattern`.

        Returns an unsubscribe callable so subscribers can detach (used
        by tests + per-request WebSocket handlers).
        """
        reg = _Registration(
            pattern=pattern,
            handler=handler,
            name=name or getattr(handler, "__name__", "anonymous"),
        )
        self._subs.append(reg)
        logger.debug(
            f"[lifecycle_bus] subscriber registered: pattern={pattern!r} "
            f"name={reg.name}"
        )

        def _unsubscribe() -> None:
            try:
                self._subs.remove(reg)
            except ValueError:
                pass

        return _unsubscribe

    # ── Emit ────────────────────────────────────────────────────────

    async def emit(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Emit an event to local subscribers + publish to Redis for
        cross-process consumers.

        Local subscribers receive synchronously inside this call; if
        any of them raises, the exception is logged and the next
        subscriber still runs (one bad subscriber doesn't break the
        bus).
        """
        evt = Event(
            name=name,
            payload=payload or {},
            source=self._self_source,
            ts=int(time.time() * 1000),
        )
        await self._dispatch_local(evt)
        await self._publish_redis(evt)

    def emit_sync(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Sync variant for callers that aren't in an event loop (DBOS
        sync workflow steps, signal handlers). Schedules the async
        emit on the current loop or runs it briefly via asyncio.run.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.emit(name, payload))
                return
        except RuntimeError:
            pass
        asyncio.run(self.emit(name, payload))

    async def _dispatch_local(self, evt: Event) -> None:
        for reg in list(self._subs):
            if not fnmatch.fnmatch(evt.name, reg.pattern):
                continue
            try:
                result = reg.handler(evt)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.opt(exception=True).warning(
                    f"[lifecycle_bus] subscriber {reg.name!r} raised on "
                    f"event {evt.name!r}: {exc}"
                )

    async def _publish_redis(self, evt: Event) -> None:
        try:
            from app.core.redis import get_async_redis

            redis = await get_async_redis()
            await redis.publish(REDIS_CHANNEL, evt.to_json())
        except Exception as exc:
            logger.opt(exception=True).debug(
                f"[lifecycle_bus] redis publish failed (degraded to "
                f"local-only): {exc}"
            )

    # ── Cross-process listener ──────────────────────────────────────

    async def start_redis_listener(self) -> None:
        """Subscribe to the Redis lifecycle channel and re-emit received
        events to the local bus. Idempotent — calling twice is a no-op.
        Cancellable via `stop_redis_listener`.
        """
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(
            self._redis_listen_loop(), name="lifecycle-bus-listener"
        )
        logger.info(f"[lifecycle_bus] redis listener started (channel={REDIS_CHANNEL})")

    async def stop_redis_listener(self, timeout: float = 2.0) -> None:
        if self._listener_task is None:
            return
        self._listener_task.cancel()
        try:
            await asyncio.wait_for(self._listener_task, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        self._listener_task = None

    async def _redis_listen_loop(self) -> None:
        from app.core.redis import get_async_redis

        backoff = 1.0
        while True:
            try:
                redis = await get_async_redis()
                pubsub = redis.pubsub()
                await pubsub.subscribe(REDIS_CHANNEL)
                backoff = 1.0  # reset on successful subscribe
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode()
                    if not isinstance(data, str):
                        continue
                    try:
                        evt = Event.from_json(data)
                    except Exception:
                        continue
                    # Drop events emitted by THIS process — we already
                    # delivered them locally inside emit(). Without this
                    # filter, every local subscriber would fire twice.
                    if evt.source == self._self_source:
                        continue
                    await self._dispatch_local(evt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.opt(exception=True).warning(
                    f"[lifecycle_bus] redis listener error: {exc} "
                    f"(reconnecting in {backoff:.1f}s)"
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2, 30.0)


# ── Singleton accessor ────────────────────────────────────────────

_bus: Optional[LifecycleBus] = None


def get_bus() -> LifecycleBus:
    """Return the process-wide lifecycle bus singleton."""
    global _bus
    if _bus is None:
        _bus = LifecycleBus()
    return _bus


__all__ = [
    "Event",
    "LifecycleBus",
    "REDIS_CHANNEL",
    "get_bus",
]
