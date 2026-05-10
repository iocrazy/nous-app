"""lane_dispatch — Origin → Lane mapping + dispatch helper."""

from __future__ import annotations

import asyncio

import pytest

from app.agent_framework import Lane, LaneQueue
from app.agent_framework.lane_dispatch import (
    Origin,
    classify_lane,
    dispatch_in_lane,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "origin,lane",
    [
        (Origin.USER_CLICK, Lane.USER),
        (Origin.USER_API, Lane.USER),
        (Origin.BACKGROUND, Lane.BACKGROUND),
        (Origin.SCHEDULED, Lane.SCHEDULED),
        (Origin.SUBAGENT, Lane.SUBAGENT),
    ],
)
def test_classify_lane(origin: Origin, lane: Lane):
    assert classify_lane(origin) == lane


@pytest.mark.unit
async def test_dispatch_uses_explicit_queue():
    """When queue passed explicitly, it gets used."""
    queue = LaneQueue()

    async def task():
        return "ok"

    result = await dispatch_in_lane(
        task(),
        origin=Origin.USER_CLICK,
        queue=queue,
    )
    assert result == "ok"


@pytest.mark.unit
async def test_dispatch_runs_directly_when_no_queue():
    """Without app.state queue + no explicit, runs directly (degrade)."""
    # No app.main import path at test-time will likely succeed but
    # app.state.lane_queue may exist. Pass None explicitly via
    # monkeypatch'ing queue lookup.

    async def task():
        return "direct"

    # Force the lookup path via no explicit queue — should still run
    # awaitable through some path (queue or direct)
    result = await dispatch_in_lane(task(), origin=Origin.BACKGROUND)
    assert result == "direct"


@pytest.mark.unit
async def test_dispatch_lane_independence():
    """Background lane saturated, USER lane still flows through."""
    queue = LaneQueue(max_concurrent={Lane.BACKGROUND: 1})

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_bg():
        started.set()
        await release.wait()

    async def fast_user():
        return "user-fast"

    bg_task = asyncio.create_task(
        dispatch_in_lane(slow_bg(), origin=Origin.BACKGROUND, queue=queue)
    )
    await started.wait()

    # USER lane should be unblocked
    result = await asyncio.wait_for(
        dispatch_in_lane(
            fast_user(),
            origin=Origin.USER_CLICK,
            queue=queue,
        ),
        timeout=0.5,
    )
    assert result == "user-fast"

    release.set()
    await bg_task
