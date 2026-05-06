"""K3 — Redis-backed ModelHealthRegistry + LifecycleBus."""
from __future__ import annotations

import json

import pytest

from app.agent_framework.lifecycle_bus import LifecycleEvent
from app.agent_framework.lifecycle_bus_redis import RedisLifecycleBus
from app.agent_framework.model_health import ModelHealth
from app.agent_framework.model_health_redis import RedisModelHealthRegistry


# ─── RedisModelHealthRegistry ────────────────────────────────────────


@pytest.mark.unit
def test_no_redis_falls_back_to_local():
    """redis_client=None → all ops local only."""
    reg = RedisModelHealthRegistry(redis_client=None)
    reg.mark_cooled_down("qwen-max", seconds=60, reason="429")
    assert reg.is_available("qwen-max") is False
    reg.mark_recovered("qwen-max")
    assert reg.is_available("qwen-max") is True


@pytest.mark.unit
def test_report_status_classifies_correctly():
    reg = RedisModelHealthRegistry(redis_client=None)
    reg.report_status("qwen-max", 429)
    assert reg.is_available("qwen-max") is False
    reg.mark_recovered("qwen-max")
    reg.report_status("qwen-max", 200)
    assert reg.is_available("qwen-max") is True


@pytest.mark.unit
def test_health_returns_state():
    reg = RedisModelHealthRegistry(redis_client=None)
    assert reg.health("unknown") == ModelHealth.AVAILABLE
    reg.mark_cooled_down("x", seconds=60, reason="test")
    assert reg.health("x") == ModelHealth.COOLED_DOWN


@pytest.mark.asyncio
async def test_async_is_available_consults_redis():
    """When Redis says cooled, async returns False even if local is clean."""
    class _FakeRedis:
        def __init__(self):
            self.keys = {"model_health:cooldown:gpt-4o"}

        async def exists(self, key):
            return 1 if key in self.keys else 0

    reg = RedisModelHealthRegistry(redis_client=_FakeRedis())
    # Redis says cooled, local hasn't heard anything yet
    assert await reg.is_available_async("gpt-4o") is False
    # Other model = available
    assert await reg.is_available_async("qwen-max") is True


@pytest.mark.asyncio
async def test_async_redis_failure_falls_back_to_local():
    """Redis exists() throws → fall back to local check (don't 500)."""
    class _BrokenRedis:
        async def exists(self, key):
            raise RuntimeError("redis down")

    reg = RedisModelHealthRegistry(redis_client=_BrokenRedis())
    reg.mark_cooled_down("qwen-max", seconds=60, reason="local")
    assert await reg.is_available_async("qwen-max") is False


@pytest.mark.unit
def test_pick_first_available_works_via_local():
    reg = RedisModelHealthRegistry(redis_client=None)
    reg.mark_cooled_down("a", seconds=60)
    pick = reg.pick_first_available(["a", "b", "c"])
    assert pick == "b"


# ─── RedisLifecycleBus ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_redis_emit_local_only():
    bus = RedisLifecycleBus(redis_client=None)
    received = []

    async def cb(event):
        received.append(event)

    bus.subscribe("test_event", cb)
    await bus.emit(LifecycleEvent(type="test_event", payload={"x": 1}))
    assert len(received) == 1
    assert received[0].payload == {"x": 1}


@pytest.mark.asyncio
async def test_emit_publishes_to_redis():
    """When Redis is wired, emit also publishes."""
    published = []

    class _FakeRedis:
        async def publish(self, channel, payload):
            published.append((channel, payload))

    bus = RedisLifecycleBus(redis_client=_FakeRedis())
    await bus.emit(LifecycleEvent(type="evt", payload={"k": "v"}))
    assert len(published) == 1
    channel, payload = published[0]
    assert channel == "lifecycle_bus:events"
    parsed = json.loads(payload)
    assert parsed["type"] == "evt"
    assert parsed["payload"] == {"k": "v"}


@pytest.mark.asyncio
async def test_emit_redis_failure_does_not_break_local():
    class _BrokenRedis:
        async def publish(self, channel, payload):
            raise RuntimeError("nope")

    bus = RedisLifecycleBus(redis_client=_BrokenRedis())
    received = []

    async def cb(event):
        received.append(event)

    bus.subscribe("evt", cb)
    # Should not raise; local subscriber still fires
    await bus.emit(LifecycleEvent(type="evt", payload={}))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_apply_remote_replays_to_local_subscribers():
    """A message arriving via Redis subscriber → fires local subscribers."""
    bus = RedisLifecycleBus(redis_client=None)
    received = []

    async def cb(event):
        received.append(event)

    bus.subscribe("remote_evt", cb)
    # Simulate remote message
    payload = json.dumps({"type": "remote_evt", "payload": {"src": "replica-B"}})
    await bus._apply_remote(payload)
    assert len(received) == 1
    assert received[0].payload["src"] == "replica-B"


@pytest.mark.asyncio
async def test_apply_remote_garbled_returns_silently():
    bus = RedisLifecycleBus(redis_client=None)
    await bus._apply_remote("not json")
    await bus._apply_remote(None)
    await bus._apply_remote(json.dumps({"missing": "type"}))
    # No exception; tests reaching here = success
