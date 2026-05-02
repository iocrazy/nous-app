"""kill_tree — graceful SIGTERM → grace → SIGKILL with Unix process
group handling.

When DBOS workflow cancel fires, mediahub spawns subprocesses (yt-dlp,
whisper, ffmpeg) that need to be killed too. Otherwise the workflow
"completes cancel" but the subprocess keeps running, holding the GPU
or filesystem locks.

The full kill sequence:
  1. SIGTERM the entire process group (parent + descendants)
  2. Wait up to ``grace_seconds`` for the process to exit voluntarily
  3. If still alive, SIGKILL the process group (force kill, no cleanup)
  4. Swallow OSError on already-dead PID (race condition is normal)

Mirrors OpenClaw ``process/kill-tree.ts`` (Unix branch). Windows path
not implemented — mediahub deploys on NAS Linux only.

Subprocesses MUST be spawned with ``preexec_fn=os.setsid`` (or its
equivalent ``start_new_session=True``) so they get their own process
group. Otherwise SIGTERM to the group hits us too. Existing subprocess
spawn sites in mediahub need a small audit; the helper itself handles
either case (will just SIGTERM the single PID if no group exists).
"""
from __future__ import annotations

import asyncio
import errno
import os
import signal
from typing import Optional

from loguru import logger


async def kill_process_tree(
    pid: int,
    *,
    grace_seconds: float = 3.0,
) -> None:
    """Kill the process tree rooted at ``pid``.

    Args:
        pid: The root process id. Treated as the process group leader.
        grace_seconds: How long to wait after SIGTERM before escalating
            to SIGKILL. Default 3s — enough for ffmpeg/yt-dlp to finish
            writing partial output, short enough that the cancel feels
            responsive in the UI.

    Behaviour:
        - PID <= 0: silent no-op (defensive; some callers store -1 as
          "no subprocess").
        - PID already dead (ProcessLookupError / OSError ESRCH):
          silent — race condition is normal and acceptable.
        - SIGTERM to the process group (negative PID per killpg
          convention). Falls back to per-PID kill if process is not a
          group leader.
        - Polls process status every 100ms during grace window.
        - SIGKILL on grace expiry; swallow ESRCH again.
    """
    if pid <= 0:
        return

    # Phase 1: SIGTERM the process group
    if not _signal_group(pid, signal.SIGTERM):
        # Couldn't even SIGTERM (likely already dead) — done.
        return

    # Phase 2: poll for voluntary exit during grace window
    deadline = asyncio.get_event_loop().time() + grace_seconds
    while asyncio.get_event_loop().time() < deadline:
        if not _is_alive(pid):
            return
        await asyncio.sleep(0.1)

    # Phase 3: still alive after grace — SIGKILL
    if _is_alive(pid):
        logger.warning(
            f"kill_tree: pid {pid} ignored SIGTERM after {grace_seconds}s, "
            f"escalating to SIGKILL"
        )
        _signal_group(pid, signal.SIGKILL)


def _signal_group(pid: int, sig: int) -> bool:
    """Send ``sig`` to the process group of ``pid`` (negative PID per
    killpg convention). Falls back to per-PID kill if not a group
    leader. Returns True if signal was sent (or process is already
    dead — same outcome semantics)."""
    # Try the group first (catches subprocess descendants)
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except ProcessLookupError:
        return True  # already dead
    except OSError as e:
        if e.errno == errno.ESRCH:
            return True  # already dead
        # Not a group leader — fall through to per-PID kill
    except Exception as e:
        logger.debug(f"kill_tree: killpg({pid}, {sig}) error: {e}")

    # Per-PID fallback
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return True
        logger.debug(f"kill_tree: kill({pid}, {sig}) error: {e}")
        return False


def _is_alive(pid: int) -> bool:
    """Return True if ``pid`` is still alive. Signal 0 is the standard
    Unix idiom for "check existence without sending a real signal"."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        # EPERM means the process exists but we're not allowed to
        # signal it — for our purposes that's "alive".
        return e.errno == errno.EPERM
