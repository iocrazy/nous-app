"""D10-7 + R2: process exit cleanup — kill all spawned children before
the parent dies.

Problem: pytest / uvicorn / dev scripts spawn multiprocessing workers
+ subprocess children (yt-dlp / whisper / ffmpeg). When the parent
crashes / SIGINT / unclean exit, those children become orphans. Each
holds connection slots in PG, leases on file handles, in-flight HTTP
requests. Eventually max_connections / FD limit hits and operator
gets cryptic "remaining connection slots reserved" errors with no
clue why.

Coverage matrix (which exit modes leave NO orphans):

  exit mode                    | atexit | signal | PR_SET_PDEATHSIG
  -----------------------------|--------|--------|------------------
  sys.exit() / normal          |   ✓    |   —    |   ✓ (already dead)
  SIGINT (Ctrl-C)              |   ✓    |   ✓    |   ✓
  SIGTERM (kill, docker stop)  |   ✓    |   ✓    |   ✓
  SIGKILL (kill -9, OOM kill)  |   ✗    |   ✗    |   ✓  ← R2 only
  power loss                   |   ✗    |   ✗    |   ✗

This module installs three layers:

  1. atexit handler — fires on normal interpreter exit. Walks the
     subprocess_registry + multiprocessing._children + kills everything
     SIGTERM → 0.5s grace → SIGKILL.

  2. Signal handlers (SIGINT, SIGTERM) — same cleanup before the
     process dies. Re-raises the signal so default behavior (exit code)
     stays correct.

  3. R2: ``bind_to_parent_death()`` — Linux-only PR_SET_PDEATHSIG
     helper for spawned children. Calling it as the first line in a
     multiprocessing target (or via os.fork() child branch) makes the
     kernel send SIGKILL to the child the instant the parent dies —
     even if the parent died via SIGKILL / OOM / panic. This is the
     only way to defend against SIGKILL of the parent.

All best-effort and idempotent — calling install_cleanup_handlers()
twice is safe.

For multiprocessing.Process children: we use multiprocessing._children
which the stdlib maintains; iterating it gives us all alive children.
"""

from __future__ import annotations

import atexit
import os
import re
import signal
import sys
from typing import Iterable, Optional

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
        prior = (
            _PRIOR_SIGINT_HANDLER if signum == signal.SIGINT else _PRIOR_SIGTERM_HANDLER
        )
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


def bind_to_parent_death() -> bool:
    """R2: Bind this process to the parent + create a new process group.

    Two effects, both pre-exec safe:

      1. ``os.setsid()`` — new session/process group. Required for
         ``kill_tree.kill_process_tree(pid)`` to actually hit all
         descendants (ffmpeg → av_demux thread, yt-dlp → curl child,
         whisper → CUDA workers). Without setsid, killpg target the
         PARENT's group → kills mediahub itself.

      2. PR_SET_PDEATHSIG — kernel will SIGKILL this child when the
         parent dies, even via SIGKILL/OOM/panic. Without this,
         atexit/signal handlers don't fire → orphans accumulate.

    POSIX (Linux + macOS) supports setsid; PR_SET_PDEATHSIG is Linux-only.
    Returns True on full success, False on Windows/error (callers
    must treat as best-effort).

    Call from a preexec_fn (subprocess.Popen / asyncio.create_subprocess_exec):

        proc = subprocess.Popen([...], **safe_popen_kwargs())

    Implementation note: PR_SET_PDEATHSIG must be set in the child
    after fork; setting it in the parent would bind the parent.
    preexec_fn satisfies this — it runs in the child between fork
    and exec.
    """
    if sys.platform == "win32":
        return False

    # 1) New process group / session — POSIX (Linux + macOS)
    try:
        os.setsid()
    except (OSError, AttributeError) as exc:
        logger.warning(f"[process_lifecycle] os.setsid failed: {exc}")
        return False

    # 2) PR_SET_PDEATHSIG — Linux only
    if sys.platform != "linux":
        return True  # macOS gets process group only; that's the best we can do

    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1  # from <sys/prctl.h>
        rc = libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if rc != 0:
            err = ctypes.get_errno()
            logger.warning(
                f"[process_lifecycle] prctl(PR_SET_PDEATHSIG) failed: errno={err}"
            )
            return False
        return True
    except Exception as exc:
        logger.warning(f"[process_lifecycle] PR_SET_PDEATHSIG failed: {exc}")
        return False


# ── Child-process environment scrubbing ───────────────────────────────────
#
# Every third-party binary this backend spawns (ffmpeg, ffprobe, yt-dlp, node
# for a_bogus signing, dreamina) used to inherit the parent's complete
# environment — nine credential-bearing variables in the production
# container as of 2026-08-26, SUPABASE_SERVICE_ROLE_KEY among them. yt-dlp
# processes attacker-controlled URLs; a crash dump, an `env` in a
# postprocessor hook, or a malicious extractor plugin would hand over the
# whole key ring. dsh's defensive-patterns rule, applied at the one
# chokepoint 45 spawn sites already splat into.
#
# Name patterns are matched case-insensitively on the variable NAME.
# `*_URL` is not a pattern (SUPABASE_URL is harmless), but any VALUE shaped
# like a credential-bearing URL (`scheme://user:pass@host`) is dropped
# regardless of name — that is what catches REDIS_URL / *_DATABASE_URL.
SECRET_NAME_PATTERNS: tuple[str, ...] = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "DSN",
    "DATABASE_URL",
)

# Names that match a pattern but are known-harmless switches. The answer to
# a false positive is a line here, never a looser pattern.
SAFE_ENV_NAMES: frozenset[str] = frozenset(
    {
        "TOKENIZERS_PARALLELISM",  # HuggingFace tokenizers thread switch
    }
)

# user part may be EMPTY (`redis://:hunter2@host` is the common redis shape);
# the password part must not be.
_CRED_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s]*:[^/@\s]+@", re.I)


def _looks_secret(name: str, value: str) -> bool:
    if name in SAFE_ENV_NAMES:
        return False
    upper = name.upper()
    if any(p in upper for p in SECRET_NAME_PATTERNS):
        return True
    return bool(_CRED_URL_RE.match(value or ""))


def scrubbed_env(
    *,
    keep: Iterable[str] = (),
    extra: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """A copy of ``os.environ`` with credential-shaped entries removed.

    ``keep`` reinstates named variables for a child that genuinely needs one
    (explicit, per call — the only escape hatch). ``extra`` adds child-
    specific variables. Always a fresh dict: mutating it never writes back
    into this process.
    """
    keep_set = set(keep)
    out = {
        k: v for k, v in os.environ.items() if k in keep_set or not _looks_secret(k, v)
    }
    if extra:
        out.update(extra)
    return out


def safe_popen_kwargs(
    *,
    env_keep: Iterable[str] = (),
    env_extra: Optional[dict[str, str]] = None,
) -> dict:
    """R2: returns kwargs to splat into subprocess.Popen / subprocess.run /
    asyncio.create_subprocess_exec so the spawned child:

      - lives in its own process group → kill_tree.kill_process_tree
        works correctly
      - auto-dies on parent SIGKILL/OOM (Linux only via PR_SET_PDEATHSIG)

    Usage:

        import subprocess
        from app.agent_framework.process_lifecycle import safe_popen_kwargs
        proc = subprocess.Popen(["yt-dlp", url], **safe_popen_kwargs())

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", src, dst, **safe_popen_kwargs()
        )

    Also carries ``env=scrubbed_env(...)`` on every platform: the child gets
    a copy of our environment with credential-shaped entries removed (see
    the scrubbing block above). A site that must hand a child one specific
    secret names it in ``env_keep``; child-specific additions go in
    ``env_extra``. Do NOT pass your own ``env=`` alongside this — the
    duplicate kwarg is a TypeError at spawn, and a test scans for it.

    On Windows: env only (no preexec_fn).
    On Linux/macOS: also passes ``preexec_fn`` (sets up new process group +
    binds-to-parent-death where supported).
    """
    kwargs: dict = {"env": scrubbed_env(keep=env_keep, extra=env_extra)}
    if sys.platform != "win32":
        kwargs["preexec_fn"] = bind_to_parent_death
    return kwargs


__all__ = [
    "install_cleanup_handlers",
    "bind_to_parent_death",
    "safe_popen_kwargs",
]
