"""Readyz gating for daemon-style background tasks.

`_bg_reap_internal_queue` / `_bg_stall_detector` are while-True loops that
never finish, so `/api/v1/readyz` reported 503 "starting" for the whole
process lifetime — readiness semantics were dead noise. Tasks spawned with
`long_running=True` invert the gate: a PENDING daemon doesn't block
readiness, but a FINISHED one (a daemon loop never returns by design, so
done == crashed) flips readyz to 503 "degraded" instead of hiding the death
behind a green probe.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.api.lifespan_router import readyz
from app.lifespan_helpers import BackgroundTaskRegistry
from app.startup.env_utils import env_int


async def _forever() -> None:
    while True:  # daemon loop — never finishes (like reap_internal_queue)
        await asyncio.sleep(3600)


async def _quick() -> None:
    await asyncio.sleep(0)


async def _crash() -> None:
    raise RuntimeError("daemon died at boot")


@pytest.fixture
async def reg():
    registry = BackgroundTaskRegistry()
    yield registry
    # Cleanup must survive a failing assert — no dangling _forever tasks
    # polluting the failure output with "Task was destroyed but pending".
    await registry.shutdown(timeout=1.0)


async def _call_readyz(registry: BackgroundTaskRegistry):
    """Drive the readyz handler on the loop that owns the tasks.

    BackgroundTaskRegistry is documented single-event-loop only; TestClient
    would run the handler on the anyio portal's loop instead. The handler
    only touches request.app.state, so a stub Request suffices.
    """
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(bg_tasks=registry))
    )
    response = Response()
    payload = await readyz(request, response)
    return response.status_code, payload


async def test_pending_daemon_does_not_block_all_done(reg):
    reg.spawn("reaper", _forever(), long_running=True)
    seed_task = reg.spawn("seed", _quick())
    await asyncio.wait_for(asyncio.shield(seed_task), timeout=1.0)
    assert reg.all_done() is True
    assert reg.dead_daemons() == []


async def test_pending_finite_task_still_blocks_all_done(reg):
    reg.spawn("slow_seed", _forever())  # NOT marked long_running
    await asyncio.sleep(0)
    assert reg.all_done() is False


async def test_crashed_daemon_flips_gate_to_not_ready(reg):
    daemon_task = reg.spawn("reaper", _crash(), long_running=True)
    with pytest.raises(RuntimeError):
        await daemon_task
    assert reg.dead_daemons() == ["reaper"]
    assert reg.all_done() is False


async def test_snapshot_flags_long_running_tasks(reg):
    reg.spawn("reaper", _forever(), long_running=True)
    seed_task = reg.spawn("seed", _quick())
    await asyncio.wait_for(asyncio.shield(seed_task), timeout=1.0)
    snapshot = {row["name"]: row for row in reg.status_snapshot()}
    assert snapshot["reaper"]["long_running"] is True
    assert snapshot["seed"]["long_running"] is False


async def test_respawn_cancels_previous_and_keeps_new_entry_clean(reg):
    old_task = reg.spawn("reaper", _forever(), long_running=True)
    new_task = reg.spawn("reaper", _forever(), long_running=True)
    with pytest.raises(asyncio.CancelledError):
        await old_task
    assert old_task.cancelled()
    # The evicted task's teardown must not stamp status onto the new entry.
    snapshot = {row["name"]: row for row in reg.status_snapshot()}
    assert snapshot["reaper"]["done"] is False
    assert snapshot["reaper"]["error"] is None
    assert not new_task.done()
    assert reg.dead_daemons() == []


async def test_readyz_ready_with_only_daemons_pending(reg):
    reg.spawn("reaper", _forever(), long_running=True)
    seed_task = reg.spawn("seed", _quick())
    await asyncio.wait_for(asyncio.shield(seed_task), timeout=1.0)

    status_code, payload = await _call_readyz(reg)
    assert status_code == 200
    assert payload["status"] == "ready"


async def test_readyz_degraded_when_daemon_crashed(reg):
    daemon_task = reg.spawn("reaper", _crash(), long_running=True)
    with pytest.raises(RuntimeError):
        await daemon_task

    status_code, payload = await _call_readyz(reg)
    assert status_code == 503
    assert payload["status"] == "degraded"
    rows = {row["name"]: row for row in payload["tasks"]}
    assert "RuntimeError" in rows["reaper"]["error"]


def test_env_int_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv("X_TEST_INTERVAL", "45")
    assert env_int("X_TEST_INTERVAL", 120) == 45
    monkeypatch.setenv("X_TEST_INTERVAL", "120s")  # the outage-class typo
    assert env_int("X_TEST_INTERVAL", 120) == 120
    monkeypatch.delenv("X_TEST_INTERVAL")
    assert env_int("X_TEST_INTERVAL", 120) == 120
