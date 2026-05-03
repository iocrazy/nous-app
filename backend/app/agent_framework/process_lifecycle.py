"""D10-7: process exit cleanup — kill all spawned children before
the parent dies.

Problem: pytest / uvicorn / dev scripts spawn multiprocessing workers
+ subprocess children (yt-dlp / whisper / ffmpeg). When the parent
crashes / SIGINT / unclean exit, those children become orphans. Each
holds connection slots in PG, leases on file handles, in-flight HTTP
requests. Eventually max_connections / FD limit hits and operator
gets cryptic "remaining connection slots reserved" errors with no
clue why.

This module installs:

  1. atexit handler — fires on normal interpreter exit. Walks the
     subprocess_registry + multiprocessing._children + kills everything
     SIGTERM → 1s grace → SIGKILL.

  2. Signal handlers (SIGINT, SIGTERM) — same cleanup before the
     process dies. Re-raises the signal so default behavior (exit code)
     stays correct.

Both are best-effort and idempotent — calling install_cleanup_handlers()
twice is safe.

For multiprocessing.Process children: we use multiprocessing._children
which the stdlib maintains; iterating it gives us all alive children.
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
from typing import Optional

from loguru import logger


_INSTALLED = False
_PRIOR_SIGINT_HANDLER = None
_PRIOR_SIGTERM_HANDLER = None


def install_cleanup_handlers() -> None:
    """Install atexit + SIGINT/SIGTERM handlers. Idempotent."""
    global _INSTALLED, _PRIOR_SIGINT_HANDLER, _PRIOR_SIGTERM_HANDLER
    if _INSTALLED:
        return

    atexit.register(_cleanup_all_children)

    # Save existing handlers so chained handler can call them
    try:
        _PRIOR_SIGINT_HANDLER = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _signal_handler)
    except (ValueError, OSError):
        # Not running in main thread — signals can only install in main
        pass

    try:
        _PRIOR_SIGTERM_HANDLER = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError):
        pass

    _INSTALLED = True


def _signal_handler(signum: int, frame) -> None:
    """SIGINT / SIGTERM → run cleanup, then re-raise so default handler
    sets the right exit code."""
    try:
        _cleanup_all_children()
    finally:
        # Restore prior handler + re-raise — keeps stack trace + exit
        # code correct
        prior = _PRIOR_SIGINT_HANDLER if signum == signal.SIGINT else _PRIOR_SIGTERM_HANDLER
        if callable(prior):
            try:
                prior(signum, frame)
                return
            except SystemExit:
                raise
            except Exception:
                pass
        # No prior handler / not callable — exit with the signal's
        # conventional exit code (128 + signum).
        sys.exit(128 + signum)


def _cleanup_all_children() -> None:
    """Kill all known children: subprocess_registry + multiprocessing.

    Best-effort — individual kill failures log but don't stop the loop.
    """
    # 1) subprocess_registry — yt-dlp / ffmpeg / whisper / etc.
    try:
        _cleanup_subprocess_registry()
    except Exception as exc:
        logger.warning(f"[process_lifecycle] subprocess_registry cleanup failed: {exc}")

    # 2) multiprocessing children — pytest workers, etc.
    try:
        _cleanup_multiprocessing_children()
    except Exception as exc:
        logger.warning(f"[process_lifecycle] multiprocessing cleanup failed: {exc}")


def _cleanup_subprocess_registry() -> None:
    """Reach into subprocess_registry's internal dict + kill_process_tree
    everything still registered."""
    from app.agent_framework import subprocess_registry as sr
    from app.agent_framework.kill_tree import kill_process_tree

    # Internal dict access — the public API is async (cancel_workflow_subprocesses);
    # at exit time we can't reliably await. Direct walk is safe-enough here.
    workflow_pids: dict = getattr(sr, "_workflow_pids", {})
    if not workflow_pids:
        return
    all_pids: set[int] = set()
    for pids in workflow_pids.values():
        if isinstance(pids, set):
            all_pids.update(pids)
        elif isinstance(pids, (list, tuple)):
            all_pids.update(pids)
    for pid in all_pids:
        try:
            # Sync kill (we can't async at exit) — kill_process_tree
            # expects async normally; fall back to direct os.kill.
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        except Exception:
            pass


def _cleanup_multiprocessing_children() -> None:
    """Send SIGTERM to multiprocessing children + give 1s grace then
    SIGKILL stragglers."""
    import multiprocessing
    import time

    children = list(multiprocessing.active_children())
    if not children:
        return
    for child in children:
        try:
            child.terminate()  # SIGTERM
        except Exception:
            pass
    # Brief grace period
    time.sleep(0.5)
    for child in children:
        try:
            if child.is_alive():
                # SIGKILL (multiprocessing.Process.kill landed in 3.7)
                if hasattr(child, "kill"):
                    child.kill()
                else:
                    os.kill(child.pid, signal.SIGKILL)
        except Exception:
            pass


__all__ = ["install_cleanup_handlers"]
