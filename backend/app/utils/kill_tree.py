"""kill_tree — graceful then forceful subprocess termination.

Why this exists
---------------
Mediahub spawns subprocesses for several long-running operations:
yt-dlp (download), ffmpeg (transcode/thumbnail), whisper (transcribe),
and per-run isolation subprocesses (PR #146). When the user cancels a
task, we need to actually stop the work — not just mark the row
cancelled while the subprocess keeps burning CPU.

Naive `proc.terminate()` only signals the immediate child. A typical
yt-dlp invocation forks a curl helper, ffmpeg muxes via a worker
thread spawning ffprobe, etc. — `terminate` leaves orphan grandchildren
that keep running until they finish naturally.

This helper does the right thing:

  1. Send SIGTERM to the entire process **group** (so children +
     grandchildren receive it). Subprocess gets a chance to clean up
     temp files, flush logs.
  2. Wait `grace_seconds` (default 3s) for the group to exit cleanly.
  3. If anything is still alive, send SIGKILL to the group. SIGKILL
     can't be caught — process dies regardless of state.
  4. Reap the immediate child via `proc.wait()` so it doesn't become
     a zombie.

The subprocess MUST have been launched with `start_new_session=True`
(or POSIX `os.setsid`) so it has its own process group; without that,
killing the group would also kill our backend. The `safe_popen_kwargs`
helper in `app.agent_framework.process_lifecycle` already sets this for
all subprocess spawns in mediahub.

Windows is not supported (no `os.killpg`); on Windows this falls back
to plain `proc.kill()` after the grace period.

Borrowed from openclaw `process/kill-tree.ts:124` line by line, with
Python idioms.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from typing import Optional, Union

from loguru import logger


def _send_group(pid: int, sig: int) -> None:
    """Send signal to the process group identified by `pid` (which must
    be the group leader's PID — i.e., the immediate child we spawned).
    Swallows ProcessLookupError because killing an already-dead group
    is fine."""
    if sys.platform == "win32":
        return  # killpg unavailable; caller falls back to direct .kill()
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        # Group already gone, or we lost permission (race with auto-reap).
        pass


def kill_tree(
    proc: Union[subprocess.Popen, "asyncio.subprocess.Process"],
    *,
    grace_seconds: float = 3.0,
) -> None:
    """SIGTERM → wait `grace_seconds` → SIGKILL. Sync version for
    use from threads or sync workflow steps.

    Args:
        proc: The subprocess.Popen (or asyncio.subprocess.Process) we
            launched. MUST have been started with
            `start_new_session=True` for group-kill to work.
        grace_seconds: Seconds to wait for graceful exit before SIGKILL.
    """
    if proc.poll() is not None:
        return  # already exited
    pid = proc.pid

    # Phase 1: graceful SIGTERM to entire group.
    _send_group(pid, signal.SIGTERM)
    if sys.platform == "win32":
        # Windows fallback — terminate the immediate child only.
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass

    # Phase 2: wait up to grace_seconds.
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    # Phase 3: SIGKILL the group + reap.
    logger.warning(
        f"[kill_tree] pid={pid} did not exit within {grace_seconds}s — "
        f"sending SIGKILL to process group"
    )
    _send_group(pid, signal.SIGKILL)
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        logger.error(
            f"[kill_tree] pid={pid} survived SIGKILL; leaking process "
            "(rare; usually a kernel-stuck syscall like NFS read)"
        )


async def kill_tree_async(
    proc: "asyncio.subprocess.Process",
    *,
    grace_seconds: float = 3.0,
) -> None:
    """Async variant for callers that have an asyncio.subprocess.Process
    and want to await the wait calls cleanly without thread-pool
    overhead.
    """
    if proc.returncode is not None:
        return
    pid = proc.pid

    _send_group(pid, signal.SIGTERM)
    if sys.platform == "win32":
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass

    logger.warning(
        f"[kill_tree_async] pid={pid} did not exit within {grace_seconds}s "
        "— sending SIGKILL to process group"
    )
    _send_group(pid, signal.SIGKILL)
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.error(
            f"[kill_tree_async] pid={pid} survived SIGKILL; leaking process"
        )


__all__ = ["kill_tree", "kill_tree_async"]
