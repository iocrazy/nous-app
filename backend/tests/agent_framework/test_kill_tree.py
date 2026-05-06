"""kill_tree — graceful SIGTERM → grace → SIGKILL with Unix process
group handling.

When DBOS workflow cancel fires, mediahub spawns subprocesses (yt-dlp,
whisper, ffmpeg) that need to be killed too. Otherwise the workflow
"completes cancel" but the subprocess keeps running, holding the GPU
or filesystem locks.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import pytest

from app.agent_framework.kill_tree import kill_process_tree


@pytest.mark.unit
async def test_kill_tree_terminates_simple_subprocess():
    """A subprocess that sleeps forever gets killed by kill_process_tree."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)",
        preexec_fn=os.setsid,  # new process group so we can signal it
    )
    try:
        # Quick check that the process started
        await asyncio.sleep(0.1)
        assert proc.returncode is None  # still running

        # Kill it
        await kill_process_tree(proc.pid, grace_seconds=1.0)

        # Wait for the process to actually exit
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        assert proc.returncode is not None  # exited
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.unit
async def test_kill_tree_kills_child_processes():
    """A subprocess that spawns its own child — both get killed via
    process-group SIGTERM. preexec_fn=os.setsid puts the parent into
    its own group; subprocess children inherit the group; killpg hits
    them all."""
    # Parent script spawns a child sleeper and waits.
    # NOTE: we do NOT call setsid inside the parent script because
    # preexec_fn already created the new session at fork time.
    parent_script = (
        "import time, subprocess; "
        "p = subprocess.Popen(['sleep', '60']); "
        "time.sleep(60)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", parent_script,
        preexec_fn=os.setsid,
    )
    try:
        await asyncio.sleep(0.3)  # let child spawn
        assert proc.returncode is None

        await kill_process_tree(proc.pid, grace_seconds=1.0)
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        assert proc.returncode is not None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.unit
async def test_kill_tree_handles_already_dead_process():
    """Killing a PID that's already gone must not raise."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass",
        preexec_fn=os.setsid,
    )
    await proc.wait()
    pid = proc.pid

    # Now PID is dead. kill_tree should swallow the error.
    await kill_process_tree(pid, grace_seconds=0.5)  # no raise


@pytest.mark.unit
async def test_kill_tree_handles_invalid_pid():
    """Negative / zero PID must not crash."""
    await kill_process_tree(0, grace_seconds=0.1)
    await kill_process_tree(-1, grace_seconds=0.1)


@pytest.mark.unit
async def test_kill_tree_escalates_to_sigkill_when_sigterm_ignored():
    """Subprocess that ignores SIGTERM still gets killed via SIGKILL."""
    # Script installs SIGTERM handler that ignores the signal,
    # then sleeps. Only SIGKILL can stop it.
    script = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        preexec_fn=os.setsid,
    )
    try:
        await asyncio.sleep(0.2)
        assert proc.returncode is None

        # grace=0.5 — SIGTERM ignored, then SIGKILL after 0.5s
        start = time.time()
        await kill_process_tree(proc.pid, grace_seconds=0.5)
        elapsed = time.time() - start

        await asyncio.wait_for(proc.wait(), timeout=2.0)
        assert proc.returncode is not None
        # Should have waited the grace period before escalating
        assert elapsed >= 0.4  # close to grace_seconds=0.5
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
