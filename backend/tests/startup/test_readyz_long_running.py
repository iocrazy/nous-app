"""Readyz must not gate on daemon-style background tasks.

`_bg_reap_internal_queue` / `_bg_stall_detector` are while-True loops that
never finish, so `/api/v1/readyz` reported 503 "starting" for the whole
process lifetime — readiness semantics were dead noise. Tasks spawned with
`long_running=True` are exempt from the `all_done()` gate while still
appearing in the status snapshot.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.lifespan_router import router as lifespan_router
from app.lifespan_helpers import BackgroundTaskRegistry


async def _forever() -> None:
    while True:  # daemon loop — never finishes (like reap_internal_queue)
        await asyncio.sleep(3600)


async def _quick() -> None:
    await asyncio.sleep(0)


async def _spin_until(predicate, timeout: float = 1.0) -> None:
    """Yield to the loop until predicate() holds (bounded)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not reached within timeout")
        await asyncio.sleep(0.01)


async def test_long_running_task_does_not_block_all_done():
    reg = BackgroundTaskRegistry()
    reg.spawn("reaper", _forever(), long_running=True)
    reg.spawn("seed", _quick())
    await _spin_until(lambda: reg._entries["seed"].task.done())
    assert reg.all_done() is True
    await reg.shutdown(timeout=1.0)


async def test_pending_normal_task_still_blocks_all_done():
    reg = BackgroundTaskRegistry()
    reg.spawn("slow_seed", _forever())  # NOT marked long_running
    await asyncio.sleep(0)
    assert reg.all_done() is False
    await reg.shutdown(timeout=1.0)


async def test_snapshot_flags_long_running_tasks():
    reg = BackgroundTaskRegistry()
    reg.spawn("reaper", _forever(), long_running=True)
    reg.spawn("seed", _quick())
    snapshot = {row["name"]: row for row in reg.status_snapshot()}
    assert snapshot["reaper"]["long_running"] is True
    assert snapshot["seed"]["long_running"] is False
    await reg.shutdown(timeout=1.0)


async def test_readyz_returns_ready_with_only_daemons_pending():
    app = FastAPI()
    app.include_router(lifespan_router, prefix="/api/v1")
    reg = BackgroundTaskRegistry()
    app.state.bg_tasks = reg
    reg.spawn("reaper", _forever(), long_running=True)
    reg.spawn("seed", _quick())
    await _spin_until(lambda: reg._entries["seed"].task.done())

    with TestClient(app) as client:
        resp = client.get("/api/v1/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    await reg.shutdown(timeout=1.0)
