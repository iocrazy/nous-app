"""Redis-backed LifecycleBus — fan-out events across replicas.

Phase K (K3). Sprint D10-2 LifecycleBus is per-process pub/sub. With
multiple replicas, agent_run_complete on replica A doesn't reach
subscribers on replica B (e.g. WebSocket pushers, dashboard counters
running on different gateways).

Mirrors I4 RedisBoundsRegistry pattern — wraps a local LifecycleBus
with Redis PUBLISH/SUBSCRIBE so emit() also broadcasts to a channel
that other replicas listen on.

Local subscribers see BOTH local emits AND remote broadcasts — same
contract as a single-process LifecycleBus.

Optional: redis_client=None falls back to pure in-process behavior.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

from app.agent_framework.lifecycle_bus import LifecycleBus, LifecycleEvent

CHANNEL = "lifecycle_bus:events"


class RedisLifecycleBus:
    """LifecycleBus + Redis fan-out. Same surface as LifecycleBus."""

    def __init__(self, redis_client: Any) -> None:
        self.redis_client = redis_client
        self.local = LifecycleBus()
        self._subscriber_task: Optional[asyncio.Task] = None

    # ─── Subscribe / emit (same surface as LifecycleBus) ───────────

    def subscribe(
        self,
        event_type: str,
        callback: Callable[[LifecycleEvent], Awaitable[None]],
    ) -> Callable[[], None]:
        return self.local.subscribe(event_type, callback)

    async def emit(self, event: LifecycleEvent) -> None:
        # 1. Local subscribers (fast path)
        await self.local.emit(event)
        # 2. Redis fan-out to other replicas (best-effort)
        if self.redis_client is None:
            return
        try:
            payload = json.dumps(
                {"type": event.type, "payload": event.payload},
                default=str,
            )
            await self.redis_client.publish(CHANNEL, payload)
        except Exception:
            pass  # never break the local emit on Redis failure

    # ─── Subscriber loop (apply remote events locally) ─────────────

    async def start_subscriber(self) -> None:
        """Spin up the asyncio task that listens for remote events.
        Idempotent — calling twice keeps the existing task."""
        if self.redis_client is None:
            return
        if self._subscriber_task is not None and not self._subscriber_task.done():
            return
        self._subscriber_task = asyncio.create_task(
            self._subscriber_loop(), name="lifecycle-bus-redis-subscriber"
        )

    async def stop_subscriber(self) -> None:
        if self._subscriber_task is None:
            return
        self._subscriber_task.cancel()
        try:
            await self._subscriber_task
        except (asyncio.CancelledError, BaseException):  # noqa: BLE001
            pass
        self._subscriber_task = None

    async def _subscriber_loop(self) -> None:
        """Listen for remote emits + replay them through local subscribers
        (so multi-replica observers get fired). Reconnect with backoff."""
        backoff = 1.0
        while True:
            try:
                pubsub = self.redis_client.pubsub()
                try:
                    await pubsub.subscribe(CHANNEL)
                    backoff = 1.0
                    async for raw in pubsub.listen():
                        if not isinstance(raw, dict) or raw.get("type") != "message":
                            continue
                        data = raw.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8", errors="ignore")
                        await self._apply_remote(data)
                finally:
                    try:
                        await pubsub.unsubscribe()
                        await pubsub.close()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2.0, 30.0)

    async def _apply_remote(self, data: Optional[str]) -> None:
        """Replay a remote event through local subscribers."""
        if not data:
            return
        try:
            obj = json.loads(data)
            event = LifecycleEvent(
                type=obj["type"],
                payload=obj.get("payload") or {},
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return
        # Replay locally — but DON'T re-publish to Redis (would loop).
        # We bypass our own emit() and call local.emit() directly.
        try:
            await self.local.emit(event)
        except Exception:
            pass


__all__ = ["CHANNEL", "RedisLifecycleBus"]
