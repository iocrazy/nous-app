"""Redis-backed BoundsRegistry decorator — multi-replica fan-out.

Wave I (I4). Sprint 5/5.5 BoundsRegistry is per-process. Single-process
deploy (current NAS): fine. Multi-replica gateway (Vercel scale-out):
gateway A doesn't see worker registered with gateway B.

This module wraps an in-process BoundsRegistry with a thin Redis layer:

  - register(bound)    → also HSET bounds:<worker_id> + PUBLISH bounds:register
  - unregister(wid)    → also HDEL + PUBLISH bounds:unregister
  - heartbeat(wid)     → also EXPIRE the HSET TTL (90s default)
  - background subscriber: listens to bounds:* channels, applies remote
    register/unregister into the local in-process registry

Result: every gateway/worker process sees the same live-bound view
within ~1s of any change.

Optional dependency: when Redis isn't configured (REDIS_HOST unset),
falls back to pure in-process behavior — same shape, no fan-out.

This module DOES NOT touch the existing BoundsRegistry class; it
returns a new RedisBoundsRegistry that satisfies the same interface.
Callers who want fan-out construct this; everyone else keeps using
the in-process one.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.agent_framework.bounds import BoundsAdvertisement, BoundsRegistry

# Redis key shape:
#   bounds:hash             → HSET worker_id → JSON-encoded bound
#   bounds:channel:register → PUBLISH on register/heartbeat
#   bounds:channel:unregister → PUBLISH on unregister/expire
HASH_KEY = "bounds:hash"
CHANNEL_REGISTER = "bounds:channel:register"
CHANNEL_UNREGISTER = "bounds:channel:unregister"

# Per-key TTL on the HSET entry (used by Redis TTL on a separate per-key
# string, since HSET doesn't have field-level TTL until Redis 7.4).
TTL_KEY_PREFIX = "bounds:ttl:"  # bounds:ttl:<worker_id>


def _serialize_bound(bound: BoundsAdvertisement) -> str:
    return json.dumps(
        {
            "worker_id": bound.worker_id,
            "role": bound.role,
            "workflows": sorted(bound.workflows),
            "agents": sorted(bound.agents),
            "providers": sorted(bound.providers),
            "lane_capacity": dict(bound.lane_capacity),
            "version": bound.version,
            "started_at": bound.started_at,
        }
    )


def _deserialize_bound(payload: str) -> Optional[BoundsAdvertisement]:
    try:
        d = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    try:
        return BoundsAdvertisement(
            worker_id=d["worker_id"],
            role=d["role"],
            workflows=frozenset(d.get("workflows") or []),
            agents=frozenset(d.get("agents") or []),
            providers=frozenset(d.get("providers") or []),
            lane_capacity=dict(d.get("lane_capacity") or {}),
            version=d.get("version"),
            started_at=float(d.get("started_at") or time.time()),
        )
    except (KeyError, ValueError, TypeError):
        return None


@dataclass
class RedisBoundsRegistry:
    """Wraps a local BoundsRegistry with Redis fan-out.

    Identical surface API as BoundsRegistry: register / heartbeat /
    unregister / live_bounds / workers_for_workflow / etc — callers
    can drop this in without changes.

    The async ``start_subscriber()`` MUST be called once at app startup
    to spin up the pubsub listener; otherwise this acts like a write-only
    proxy + the local view stays in-process-only on the read side.
    """

    redis_client: Any  # async redis.Redis instance (None = act like plain in-proc)
    local: BoundsRegistry
    ttl_seconds: int = 90
    _subscriber_task: Optional[asyncio.Task] = None

    @classmethod
    def create(
        cls, redis_client: Any, *, ttl_seconds: int = 90
    ) -> "RedisBoundsRegistry":
        return cls(
            redis_client=redis_client,
            local=BoundsRegistry(stale_after_s=float(ttl_seconds)),
            ttl_seconds=ttl_seconds,
        )

    # ─── Local read API (delegates to in-process registry) ────────

    def live_bounds(self):
        return self.local.live_bounds()

    def workers_for_workflow(self, workflow_name: str):
        return self.local.workers_for_workflow(workflow_name)

    def workers_for_agent(self, agent_slug: str):
        return self.local.workers_for_agent(agent_slug)

    def can_dispatch_workflow(self, workflow_name: str) -> bool:
        return self.local.can_dispatch_workflow(workflow_name)

    def snapshot(self):
        return self.local.snapshot()

    # ─── Write API: local + Redis fan-out ─────────────────────────

    def register(self, bound: BoundsAdvertisement) -> None:
        self.local.register(bound)
        if self.redis_client is None:
            return
        asyncio.create_task(self._publish_register(bound))

    def heartbeat(self, worker_id: str) -> bool:
        ok = self.local.heartbeat(worker_id)
        if ok and self.redis_client is not None:
            asyncio.create_task(self._publish_heartbeat(worker_id))
        return ok

    def unregister(self, worker_id: str) -> None:
        self.local.unregister(worker_id)
        if self.redis_client is None:
            return
        asyncio.create_task(self._publish_unregister(worker_id))

    # ─── Async Redis I/O ──────────────────────────────────────────

    async def _publish_register(self, bound: BoundsAdvertisement) -> None:
        try:
            payload = _serialize_bound(bound)
            await self.redis_client.hset(HASH_KEY, bound.worker_id, payload)
            await self.redis_client.set(
                f"{TTL_KEY_PREFIX}{bound.worker_id}", "1", ex=self.ttl_seconds
            )
            await self.redis_client.publish(CHANNEL_REGISTER, payload)
        except Exception:
            pass  # best-effort; in-process registry still has it

    async def _publish_heartbeat(self, worker_id: str) -> None:
        try:
            await self.redis_client.expire(
                f"{TTL_KEY_PREFIX}{worker_id}", self.ttl_seconds
            )
            await self.redis_client.publish(
                CHANNEL_REGISTER, json.dumps({"worker_id": worker_id, "_hb": True})
            )
        except Exception:
            pass

    async def _publish_unregister(self, worker_id: str) -> None:
        try:
            await self.redis_client.hdel(HASH_KEY, worker_id)
            await self.redis_client.delete(f"{TTL_KEY_PREFIX}{worker_id}")
            await self.redis_client.publish(CHANNEL_UNREGISTER, worker_id)
        except Exception:
            pass

    # ─── Subscriber loop ──────────────────────────────────────────

    async def start_subscriber(self) -> None:
        """Spin up an asyncio task that listens for register/unregister
        messages from other replicas + applies them locally.

        Idempotent — calling twice keeps the existing task.
        """
        if self.redis_client is None:
            return
        if self._subscriber_task is not None and not self._subscriber_task.done():
            return
        self._subscriber_task = asyncio.create_task(
            self._subscriber_loop(), name="bounds-redis-subscriber"
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
        """Listen on the two channels; apply messages to the local
        registry. Reconnect on transient failures with exponential
        backoff capped at 30s."""
        backoff = 1.0
        while True:
            try:
                pubsub = self.redis_client.pubsub()
                try:
                    await pubsub.subscribe(CHANNEL_REGISTER, CHANNEL_UNREGISTER)
                    backoff = 1.0  # reset on successful connect
                    async for raw in pubsub.listen():
                        if not isinstance(raw, dict):
                            continue
                        msg_type = raw.get("type")
                        if msg_type != "message":
                            continue
                        channel = raw.get("channel")
                        if isinstance(channel, bytes):
                            channel = channel.decode("utf-8", errors="ignore")
                        data = raw.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8", errors="ignore")
                        self._apply_remote(channel, data)
                finally:
                    try:
                        await pubsub.unsubscribe()
                        await pubsub.close()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
            except Exception:
                # transient — back off + retry
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2.0, 30.0)

    def _apply_remote(self, channel: Optional[str], data: Optional[str]) -> None:
        if not channel or data is None:
            return
        if channel == CHANNEL_REGISTER:
            try:
                obj = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return
            if obj.get("_hb"):
                self.local.heartbeat(obj.get("worker_id", ""))
                return
            bound = _deserialize_bound(data)
            if bound is not None:
                self.local.register(bound)
        elif channel == CHANNEL_UNREGISTER:
            self.local.unregister(data)


__all__ = [
    "CHANNEL_REGISTER",
    "CHANNEL_UNREGISTER",
    "HASH_KEY",
    "RedisBoundsRegistry",
]
