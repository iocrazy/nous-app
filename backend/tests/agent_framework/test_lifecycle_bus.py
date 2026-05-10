"""LifecycleBus — in-process pub/sub for lifecycle events.

Decouples emitters (workflow start/complete/fail, agent_run record,
Discord notify, Sentry capture, Realtime push) from each other. Each
listener subscribes to event types it cares about. Listener exceptions
are isolated — a buggy Discord notifier can't break the workflow that
emitted the event.

Mirrors OpenClaw sessions/session-lifecycle-events.ts (28-line module
that punches above its weight).
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_framework.lifecycle_bus import (
    LifecycleBus,
    LifecycleEvent,
)


@pytest.mark.unit
async def test_emit_to_no_listeners_is_silent():
    bus = LifecycleBus()
    await bus.emit(LifecycleEvent(type="workflow.start", payload={"id": "1"}))
    # No raise


@pytest.mark.unit
async def test_listener_receives_emitted_events():
    bus = LifecycleBus()
    received: list[LifecycleEvent] = []

    async def listener(event: LifecycleEvent) -> None:
        received.append(event)

    bus.subscribe("workflow.start", listener)
    await bus.emit(LifecycleEvent(type="workflow.start", payload={"x": 1}))
    assert len(received) == 1
    assert received[0].type == "workflow.start"
    assert received[0].payload == {"x": 1}


@pytest.mark.unit
async def test_listener_only_receives_subscribed_type():
    bus = LifecycleBus()
    received: list[LifecycleEvent] = []

    async def listener(event: LifecycleEvent) -> None:
        received.append(event)

    bus.subscribe("workflow.start", listener)
    await bus.emit(LifecycleEvent(type="workflow.complete", payload={}))
    await bus.emit(LifecycleEvent(type="workflow.start", payload={}))
    assert len(received) == 1
    assert received[0].type == "workflow.start"


@pytest.mark.unit
async def test_multiple_listeners_all_invoked():
    bus = LifecycleBus()
    a_calls: list[int] = []
    b_calls: list[int] = []

    async def a(event):
        a_calls.append(1)

    async def b(event):
        b_calls.append(1)

    bus.subscribe("test", a)
    bus.subscribe("test", b)
    await bus.emit(LifecycleEvent(type="test", payload={}))
    assert len(a_calls) == 1
    assert len(b_calls) == 1


@pytest.mark.unit
async def test_listener_exception_does_not_break_others():
    """The load-bearing contract: a buggy listener can't poison the bus."""
    bus = LifecycleBus()
    good_received: list[int] = []

    async def buggy(event):
        raise RuntimeError("listener exploded")

    async def good(event):
        good_received.append(1)

    bus.subscribe("test", buggy)
    bus.subscribe("test", good)
    # Emit MUST succeed (returns normally, just logs the buggy listener)
    await bus.emit(LifecycleEvent(type="test", payload={}))
    assert good_received == [1]


@pytest.mark.unit
async def test_unsubscribe_via_returned_handle():
    """subscribe returns a handle that can be called to unsubscribe."""
    bus = LifecycleBus()
    received: list[int] = []

    async def listener(event):
        received.append(1)

    unsubscribe = bus.subscribe("test", listener)
    await bus.emit(LifecycleEvent(type="test", payload={}))
    assert received == [1]

    unsubscribe()
    await bus.emit(LifecycleEvent(type="test", payload={}))
    assert received == [1]  # no new event delivered


@pytest.mark.unit
async def test_subscribe_wildcard_receives_all():
    """Subscribing to '*' receives every emitted event regardless of type."""
    bus = LifecycleBus()
    received: list[str] = []

    async def listener(event):
        received.append(event.type)

    bus.subscribe("*", listener)
    await bus.emit(LifecycleEvent(type="workflow.start", payload={}))
    await bus.emit(LifecycleEvent(type="workflow.complete", payload={}))
    await bus.emit(LifecycleEvent(type="agent.run", payload={}))
    assert received == ["workflow.start", "workflow.complete", "agent.run"]


@pytest.mark.unit
async def test_listeners_run_concurrently():
    """All listeners for an event are dispatched concurrently, not
    sequentially. Slow listener doesn't block fast listeners."""
    bus = LifecycleBus()

    slow_done = asyncio.Event()
    fast_done = asyncio.Event()

    async def slow(event):
        await asyncio.sleep(0.5)
        slow_done.set()

    async def fast(event):
        await asyncio.sleep(0.01)
        fast_done.set()

    bus.subscribe("test", slow)
    bus.subscribe("test", fast)

    emit_task = asyncio.create_task(bus.emit(LifecycleEvent(type="test", payload={})))
    # Fast listener should finish first
    await asyncio.wait_for(fast_done.wait(), timeout=0.3)
    assert not slow_done.is_set()
    await emit_task
    assert slow_done.is_set()


@pytest.mark.unit
async def test_sync_listener_supported():
    """Sync (non-async) listeners are also supported — wrapped at
    dispatch time. Existing modules that do `def on_event(e):` get
    backwards-compat without rewriting."""
    bus = LifecycleBus()
    received: list[int] = []

    def sync_listener(event):
        received.append(1)

    bus.subscribe("test", sync_listener)  # type: ignore[arg-type]
    await bus.emit(LifecycleEvent(type="test", payload={}))
    assert received == [1]
