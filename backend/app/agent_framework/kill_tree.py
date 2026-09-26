"""kill_tree — SIGTERM → grace → SIGKILL on a whole process group, returning
only once the group is STILL (CLAUDE.md 防御模式「Dispose 必须到达静止」).

When DBOS workflow cancel fires, nous spawns subprocesses (yt-dlp,
whisper, ffmpeg) that need to be killed too. Otherwise the workflow
"completes cancel" but the subprocess keeps running, holding the GPU
or filesystem locks.

The kill sequence:
  1. Capture the child's process group ONCE, before any signal. Every later
     signal goes to that pgid — it keeps reaching the grandchildren after the
     leader has died (``getpgid(pid)`` raises ESRCH once the leader is gone,
     which is how yt-dlp's ffmpeg used to survive).
  2. SIGTERM the group; poll for quiescence until ``grace_seconds``.
  3. SIGKILL the group; poll again, bounded by ``reap_timeout_s``.
  4. Report a frozen ``KillOutcome``. ``quiesced=False`` is logged at ERROR:
     that is the case an operator must see.

"Quiescent" = the leader is not running AND no member of the group is
running. A zombie counts as not running: it holds no resources beyond its
process-table slot and is waiting for its owner's ``wait``. We only PEEK at
our own children (``waitid(..., WNOWAIT)``) and never reap them — reaping
would steal the exit status from ``asyncio``'s child watcher or from
``Popen.wait`` (which then reports rc 255 / 0 instead of the real signal).

If the child shares OUR process group (spawned without a new session),
only its pid is signalled — ``killpg`` on it would kill us.

Subprocesses SHOULD be spawned with ``**safe_popen_kwargs()`` (setsid +
PDEATHSIG), or ``start_new_session=True`` for the one env-exempt site.

Mirrors OpenClaw ``process/kill-tree.ts`` (Unix branch). Windows is not
implemented — nous deploys on Linux; macOS is supported for development.
"""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Generator

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs

_POLL_S = 0.05
DEFAULT_REAP_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class KillOutcome:
    """How one kill went. Independent flags (orthogonal reporting)."""

    already_dead: bool  # nothing was running when we arrived; no signal sent
    exited_on_term: bool  # the group went still within the SIGTERM grace
    escalated: bool  # SIGKILL was needed
    quiesced: bool  # the group is still now (False = logged at ERROR)


async def kill_process_tree(
    pid: int,
    *,
    grace_seconds: float = 3.0,
    reap_timeout_s: float = DEFAULT_REAP_TIMEOUT_S,
) -> KillOutcome:
    """Kill the process tree rooted at ``pid`` and wait until it is still.

    ``pid <= 0`` is a silent no-op (some callers store -1 as "no child").
    """
    steps = _kill_steps(pid, grace_seconds, reap_timeout_s)
    try:
        while True:
            await asyncio.sleep(next(steps))
    except StopIteration as done:
        return done.value


def kill_process_tree_sync(
    pid: int,
    *,
    grace_seconds: float = 0.5,
    reap_timeout_s: float = DEFAULT_REAP_TIMEOUT_S,
) -> KillOutcome:
    """Blocking twin for contexts that cannot await: atexit, signal
    handlers, and the sync ``isolated_runner`` step."""
    steps = _kill_steps(pid, grace_seconds, reap_timeout_s)
    try:
        while True:
            time.sleep(next(steps))
    except StopIteration as done:
        return done.value


def _kill_steps(
    pid: int, grace_seconds: float, reap_timeout_s: float
) -> Generator[float, None, KillOutcome]:
    """The kill sequence, written once. Yields sleep durations; the async and
    sync drivers differ only in how they sleep."""
    if pid <= 0:
        return KillOutcome(True, False, False, True)
    pgid = _capture_pgid(pid)
    if _quiescent(pid, pgid):
        return KillOutcome(True, False, False, True)

    _send(pid, pgid, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_seconds, 0.0)
    while time.monotonic() < deadline:
        if _quiescent(pid, pgid):
            return KillOutcome(False, True, False, True)
        yield _POLL_S
    if _quiescent(pid, pgid):
        return KillOutcome(False, True, False, True)

    logger.warning(
        f"kill_tree: pid {pid} (pgid {pgid}) still running {grace_seconds}s "
        "after SIGTERM, escalating to SIGKILL"
    )
    _send(pid, pgid, signal.SIGKILL)
    deadline = time.monotonic() + max(reap_timeout_s, 0.0)
    while time.monotonic() < deadline:
        if _quiescent(pid, pgid):
            return KillOutcome(False, False, True, True)
        yield _POLL_S
    quiesced = _quiescent(pid, pgid)
    if not quiesced:
        logger.error(
            f"kill_tree: pid {pid} (pgid {pgid}) NOT quiescent {reap_timeout_s}s "
            "after SIGKILL — process or group member left running"
        )
    return KillOutcome(False, False, True, quiesced)


def _capture_pgid(pid: int) -> int | None:
    """The group to signal, or ``None`` when only the pid may be signalled
    (no group found, or it is OUR group)."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None
    if pgid == os.getpgrp():
        return None
    return pgid


def _send(pid: int, pgid: int | None, sig: int) -> None:
    """Signal the captured group (or the lone pid). ESRCH = already gone,
    which is the outcome we wanted."""
    try:
        if pgid is not None:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        return
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            logger.warning(f"kill_tree: signal {sig} to pid={pid} pgid={pgid}: {exc}")


def _quiescent(pid: int, pgid: int | None) -> bool:
    return _is_dead(pid) and (pgid is None or _group_quiescent(pgid))


def _is_dead(pid: int) -> bool:
    """``pid`` is not running: gone, or a zombie awaiting its reaper.

    Our own child is PEEKED at with ``waitid(WNOWAIT)`` — never reaped, so
    the owner's ``wait`` still gets the real status. Anyone else's pid falls
    back to ``kill(pid, 0)`` plus a zombie check.
    """
    try:
        res = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        res = None  # not our child (or already reaped) — probe below
    except (AttributeError, OSError):
        res = None
    else:
        # Our child: a result means it has exited and waits to be reaped.
        # None means it is still running.
        return res is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # exists, not ours to signal
    except OSError as exc:
        return exc.errno == errno.ESRCH
    return _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """Linux reads ``/proc/<pid>/stat`` field 3; macOS asks ``ps``."""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
                data = fh.read()
        except OSError:
            return True  # vanished between the probes
        tail = data.rsplit(")", 1)[-1].split()
        return bool(tail) and tail[0] == "Z"
    states = _ps_states(["-p", str(pid)])
    return not states or all(s.startswith("Z") for s in states)


def _group_quiescent(pgid: int) -> bool:
    """No member of ``pgid`` is running (members that are zombies are fine)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return True
    states = _group_states(pgid)
    if states is None:
        return False  # could not enumerate; the group still answers signal 0
    return all(s.startswith("Z") for s in states)


def _group_states(pgid: int) -> list[str] | None:
    """Process states of every member of ``pgid``."""
    if sys.platform.startswith("linux"):
        states: list[str] = []
        try:
            entries = os.listdir("/proc")
        except OSError:
            return None
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(
                    f"/proc/{entry}/stat", encoding="ascii", errors="replace"
                ) as fh:
                    tail = fh.read().rsplit(")", 1)[-1].split()
            except OSError:
                continue
            # tail: state ppid pgrp ...
            if len(tail) > 2 and tail[2] == str(pgid):
                states.append(tail[0])
        return states
    rows = _ps_rows(["-A", "-o", "pgid=,stat="])
    if rows is None:
        return None
    return [stat for g, stat in rows if g == str(pgid)]


def _ps_rows(args: list[str]) -> list[tuple[str, str]] | None:
    try:
        out = subprocess.run(
            ["ps", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            **safe_popen_kwargs(),
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"kill_tree: ps {args} failed: {exc}")
        return None
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _ps_states(args: list[str]) -> list[str]:
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            **safe_popen_kwargs(),
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"kill_tree: ps {args} failed: {exc}")
        return []
    return [s.strip() for s in out.splitlines() if s.strip()]


__all__ = ["KillOutcome", "kill_process_tree", "kill_process_tree_sync"]
