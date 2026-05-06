"""subprocess_registry — track child PIDs per workflow_id."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from app.agent_framework.subprocess_registry import (
    cancel_workflow_subprocesses,
    clear_registry,
    register_subprocess,
    registered_pids,
    unregister_subprocess,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.mark.unit
def test_register_and_query():
    register_subprocess("wf-1", 12345)
    register_subprocess("wf-1", 67890)
    pids = registered_pids("wf-1")
    assert sorted(pids) == [12345, 67890]


@pytest.mark.unit
def test_unregister_removes_pid():
    register_subprocess("wf-1", 12345)
    register_subprocess("wf-1", 67890)
    unregister_subprocess("wf-1", 12345)
    assert registered_pids("wf-1") == [67890]


@pytest.mark.unit
def test_unregister_last_pid_drops_workflow_entry():
    register_subprocess("wf-1", 12345)
    unregister_subprocess("wf-1", 12345)
    assert registered_pids("wf-1") == []


@pytest.mark.unit
def test_register_with_falsy_inputs_silent():
    register_subprocess(None, 12345)
    register_subprocess("", 12345)
    register_subprocess("wf-1", 0)
    assert registered_pids("wf-1") == []


@pytest.mark.unit
def test_unregister_missing_silent():
    """Unregister on a workflow we never registered — no raise."""
    unregister_subprocess("never-registered", 99999)


@pytest.mark.unit
async def test_cancel_workflow_subprocesses_kills_real_subprocess():
    """End-to-end: spawn a real sleeper subprocess, register, cancel."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)",
        preexec_fn=os.setsid,
    )
    try:
        register_subprocess("wf-real", proc.pid)
        await asyncio.sleep(0.1)  # let it start
        assert proc.returncode is None

        count = await cancel_workflow_subprocesses("wf-real", grace_seconds=1.0)
        assert count == 1

        # Subprocess should now exit
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        assert proc.returncode is not None

        # Registry entry dropped
        assert registered_pids("wf-real") == []
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.unit
async def test_cancel_no_pids_returns_zero():
    count = await cancel_workflow_subprocesses("never-registered")
    assert count == 0


@pytest.mark.unit
async def test_cancel_multiple_pids_continues_on_individual_failure(monkeypatch):
    """Bad PID in registry doesn't prevent other PIDs from being killed."""
    from app.agent_framework import subprocess_registry as sr

    register_subprocess("wf-mixed", 12345)
    register_subprocess("wf-mixed", 67890)

    killed: list[int] = []

    async def fake_kill(pid, *, grace_seconds=3.0):
        killed.append(pid)
        if pid == 12345:
            raise RuntimeError("simulated kill failure for 12345")

    monkeypatch.setattr(sr, "kill_process_tree", fake_kill)
    count = await cancel_workflow_subprocesses("wf-mixed")
    # Both attempted; one raised; count = 1 (successful)
    assert sorted(killed) == [12345, 67890]
    assert count == 1
    # Registry still cleared
    assert registered_pids("wf-mixed") == []
