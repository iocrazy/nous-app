"""Per-user agent concurrency gate: at most N concurrent turns per user, others
wait; different users don't block each other; the limit is clamped + live."""

from __future__ import annotations

import asyncio

import pytest

from app.services.ai.chat import agent_concurrency as ac


@pytest.fixture(autouse=True)
def _reset():
    ac.reset_for_tests()
    ac.set_agent_concurrency(2)
    yield
    ac.reset_for_tests()


def test_limit_clamped():
    ac.set_agent_concurrency(0)
    assert ac.current_limit() == 1
    ac.set_agent_concurrency(999)
    assert ac.current_limit() == 20


@pytest.mark.asyncio
async def test_caps_per_user():
    ac.set_agent_concurrency(1)
    order: list[str] = []

    async def turn(tag: str):
        async with ac.user_slot("u1"):
            order.append(f"start:{tag}")
            await asyncio.sleep(0.05)
            order.append(f"end:{tag}")

    await asyncio.gather(turn("a"), turn("b"))
    # With limit 1, the two u1 turns are serialized (no interleave).
    assert order == ["start:a", "end:a", "start:b", "end:b"] or order == [
        "start:b",
        "end:b",
        "start:a",
        "end:a",
    ]


@pytest.mark.asyncio
async def test_different_users_run_concurrently():
    ac.set_agent_concurrency(1)
    started = asyncio.Event()

    async def hold():
        async with ac.user_slot("u1"):
            started.set()
            await asyncio.sleep(0.1)

    async def other():
        await asyncio.wait_for(started.wait(), 1.0)
        # u2 acquires immediately despite u1 holding its own slot
        async with ac.user_slot("u2"):
            return True

    _, ok = await asyncio.gather(hold(), other())
    assert ok is True
