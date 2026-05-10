"""Unit tests for AgentWorkerPool — paperclip-style in-process scheduler.

Pins the three concurrency contracts:
1. Same-agent dispatches serialise via per-agent asyncio.Lock
2. Cross-agent dispatches run in parallel
3. Shutdown drains in-flight tasks (with timeout fallback to cancel)

Plus the failure-isolation contract: a runner exception doesn't poison
the pool — subsequent dispatches still work.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.workforce.worker_pool import AgentWorkerPool

# ─── helpers ──────────────────────────────────────────────────────────


def _task(agent_id: UUID, task_id: UUID | None = None) -> dict[str, Any]:
    return {
        "id": str(task_id or uuid4()),
        "agent_id": str(agent_id),
        "lifecycle_status": "queued",
        "payload": {},
    }


# ─── per-agent serialisation ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_agent_dispatches_run_serially():
    """Two tasks for the same agent must NOT overlap. Second one waits
    for first to finish (+/- a tiny scheduler slack)."""
    agent_id = uuid4()
    log: list[tuple[str, str]] = []
    enter = asyncio.Event()
    proceed = asyncio.Event()

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        log.append(("enter", task["id"]))
        if not enter.is_set():
            enter.set()
            # Block here so the second dispatch has to wait on the lock.
            await proceed.wait()
        log.append(("exit", task["id"]))
        return {}

    pool = AgentWorkerPool(runner=runner)
    t1 = _task(agent_id, uuid4())
    t2 = _task(agent_id, uuid4())

    await pool.dispatch(t1)
    await enter.wait()  # first runner is now inside the lock
    await pool.dispatch(t2)

    # Give event loop a chance to scheduler t2's create_task. Even with
    # the full slice yielded, t2 shouldn't have entered the runner yet
    # — the lock is held by t1.
    await asyncio.sleep(0.05)
    assert log == [
        ("enter", t1["id"])
    ], "t2 must not have entered runner while t1 holds the lock"

    # Release t1; both should complete.
    proceed.set()
    await pool.shutdown(drain_timeout=2.0)

    sequence = [step for step, _ in log]
    assert sequence == [
        "enter",
        "exit",
        "enter",
        "exit",
    ], f"expected serial order, got {log}"


# ─── cross-agent parallelism ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_different_agents_run_in_parallel():
    """Different agents must not share locks — both runners run
    concurrently."""
    enters: dict[str, asyncio.Event] = {"a": asyncio.Event(), "b": asyncio.Event()}
    proceed = asyncio.Event()

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        # Mark which agent entered, then wait for proceed signal so we
        # can prove BOTH entered before EITHER exited.
        agent_label = task["payload"]["label"]
        enters[agent_label].set()
        await proceed.wait()
        return {}

    pool = AgentWorkerPool(runner=runner)
    t_a = {**_task(uuid4()), "payload": {"label": "a"}}
    t_b = {**_task(uuid4()), "payload": {"label": "b"}}

    await pool.dispatch(t_a)
    await pool.dispatch(t_b)

    # Both should enter without anyone needing to release a lock.
    await asyncio.wait_for(
        asyncio.gather(enters["a"].wait(), enters["b"].wait()),
        timeout=1.0,
    )
    proceed.set()
    await pool.shutdown(drain_timeout=2.0)


# ─── failure isolation ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runner_exception_does_not_break_pool():
    """A crashing runner is logged and swallowed — subsequent dispatches
    still work."""
    agent_id = uuid4()
    success_calls: list[str] = []

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        if task["payload"].get("crash"):
            raise RuntimeError("boom")
        success_calls.append(task["id"])
        return {}

    pool = AgentWorkerPool(runner=runner)
    t_crash = {**_task(agent_id), "payload": {"crash": True}}
    t_ok = {**_task(agent_id), "payload": {}}

    await pool.dispatch(t_crash)
    await pool.dispatch(t_ok)
    await pool.shutdown(drain_timeout=2.0)

    assert success_calls == [t_ok["id"]]


# ─── missing agent_id ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_drops_task_with_missing_agent_id():
    """Malformed task → log + drop, don't crash."""
    called: list[str] = []

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        called.append(task["id"])
        return {}

    pool = AgentWorkerPool(runner=runner)
    await pool.dispatch({"id": "bad", "agent_id": None})
    await pool.shutdown(drain_timeout=1.0)
    assert called == []


# ─── shutdown drain ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_drains_inflight_within_timeout():
    """A runner that finishes within drain_timeout should complete
    normally — no cancellation."""
    finished: list[str] = []
    proceed = asyncio.Event()

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        await proceed.wait()
        finished.append(task["id"])
        return {}

    pool = AgentWorkerPool(runner=runner)
    t = _task(uuid4())
    await pool.dispatch(t)

    # Let it sit in the runner.
    await asyncio.sleep(0.05)
    assert pool.inflight_count == 1

    # Release immediately; drain finishes well within budget.
    proceed.set()
    await pool.shutdown(drain_timeout=2.0)
    assert finished == [t["id"]]
    assert pool.inflight_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_cancels_runners_past_drain_timeout():
    """Runners that ignore drain are hard-cancelled."""
    cancelled = asyncio.Event()

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        try:
            await asyncio.sleep(60)  # way past drain
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}

    pool = AgentWorkerPool(runner=runner)
    await pool.dispatch(_task(uuid4()))
    await asyncio.sleep(0.05)  # let runner enter

    await pool.shutdown(drain_timeout=0.1)
    assert cancelled.is_set()


# ─── post-shutdown idempotency ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_after_shutdown_is_dropped():
    """Once shutdown completes, dispatch should silently drop tasks
    rather than raise — uvicorn shouldn't crash on shutdown races."""
    called: list[str] = []

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        called.append(task["id"])
        return {}

    pool = AgentWorkerPool(runner=runner)
    await pool.shutdown(drain_timeout=0.5)
    await pool.dispatch(_task(uuid4()))
    # No new run should fire.
    await asyncio.sleep(0.05)
    assert called == []


# ─── lock keying — per-agent, not per-task ────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_known_agents_records_distinct_agent_ids():
    """The per-agent lock map keys on agent_id, not task_id."""
    a1, a2 = uuid4(), uuid4()
    proceed = asyncio.Event()

    async def runner(task: dict[str, Any]) -> dict[str, Any]:
        await proceed.wait()
        return {}

    pool = AgentWorkerPool(runner=runner)
    await pool.dispatch(_task(a1, uuid4()))
    await pool.dispatch(_task(a1, uuid4()))  # same agent, different task
    await pool.dispatch(_task(a2, uuid4()))

    await asyncio.sleep(0.05)
    assert pool.known_agents == {a1, a2}

    proceed.set()
    await pool.shutdown(drain_timeout=2.0)
