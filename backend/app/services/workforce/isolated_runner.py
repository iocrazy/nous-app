"""Subprocess wrapper around `app/run_isolated.py` — PR-D8 Phase 2.

Spawns a fresh Python interpreter for one agent task, applies POSIX
resource limits (RLIMIT_AS for memory cap), enforces a wall-clock
timeout, and translates exit codes into the same dict envelope the
in-process `run_one_task` returns.

Why subprocess not threading or multiprocessing.Pool
----------------------------------------------------
* threading: shares the GIL + heap; defeats the isolation goal.
* multiprocessing.Pool: workers are reused across tasks, so leaks
  accumulate just like in the parent. The whole point is "fresh
  process per run", which Pool doesn't give us.
* asyncio.create_subprocess_exec: would work, but task here is
  end-to-end synchronous from the worker's perspective (DBOS step
  is sync), so the simpler `subprocess.Popen` API is enough.

Result mapping (orthogonal — CLAUDE.md「正交的结果各自独立上报」)
---------------------------------------------------------------
``timed_out`` / ``signal`` / ``exit_code`` are independent fields on
:class:`IsolatedRunResult`, taken from the REAL reaped status:

    returncode >= 0   — ``exit_code`` = returncode, ``signal`` = None.
    returncode <  0   — ``signal`` = -returncode (subprocess's convention for
                        a signal death), ``exit_code`` = None.
    our deadline      — ``timed_out`` = True, error_code ``timeout``, whatever
                        the reaped status says (a child that traps SIGTERM
                        and exits 0 is still a timeout).

Known signal deaths map to friendly error codes: SIGKILL → ``oom_killed``
(the kernel OOM killer, or RLIMIT_AS), SIGSEGV → ``segfault``, SIGTERM →
``terminated``. A literal ``exit(137)`` is an exit code, not a SIGKILL, and
lands in ``runtime_error``. (The old 124/137/139/143 map could never match: a
signal death is reported as a NEGATIVE returncode.)

On timeout the whole process group (the child runs in its own session) gets
SIGTERM → grace → SIGKILL and is reaped; the envelope the child already
printed is kept.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from app.agent_framework.kill_tree import kill_process_tree_sync


@dataclass(frozen=True)
class IsolatedRunResult:
    """Envelope returned to callers regardless of how the run terminated."""

    status: str  # "done" | "failed" | "skipped"
    task_id: Optional[str]
    run_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    exit_code: Optional[int]  # normal exit status; None if a signal killed it
    # The wake ORDER a background sub-agent's branch produced, carried through
    # the wire so subprocess mode delivers what in-process mode delivers. A
    # key dropped here is not an error anywhere — it is a wake-up that simply
    # never happens, in exactly one deployment mode.
    idle_dispatch: Optional[Dict[str, Any]] = None
    timed_out: bool = False  # OUR wall-clock deadline fired
    signal: Optional[int] = None  # terminating signal, from the reaped status

    def as_worker_dict(self) -> Dict[str, Any]:
        """Match the shape `run_one_task` returns so callers can swap
        the in-process call for `run_isolated()` with no further changes
        upstream."""
        out: Dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "run_id": self.run_id,
            "idle_dispatch": self.idle_dispatch,
        }
        if self.error_code:
            out["reason"] = self.error_code
        return out


def _build_preexec_fn(mem_limit_bytes: Optional[int]):
    """Construct a preexec_fn that sets RLIMIT_AS before exec.

    Only POSIX has resource(7); Windows callers will set this to None.
    The function runs in the child *between fork and exec* — keep it
    minimal, no I/O, no allocations beyond what the syscall needs.
    """
    if mem_limit_bytes is None or mem_limit_bytes <= 0:
        return None
    if sys.platform == "win32":
        return None

    import resource  # noqa: F401 — guarded above; only imported when used

    def _preexec() -> None:
        # RLIMIT_AS = max virtual memory the process may map. Hitting it
        # makes mmap / brk fail (typically MemoryError in Python). On
        # Linux the OOM killer may also fire; either way, the parent
        # treats the result as "child OOMed" via exit code 137.
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes))
        except (ValueError, OSError):
            # macOS RLIMIT_AS is advisory at best, sometimes rejected.
            # Don't crash exec — fall back to soft enforcement via the
            # wall-clock + parent monitoring.
            pass

    return _preexec


def run_isolated(
    task: Dict[str, Any],
    *,
    timeout_s: float,
    mem_limit_mb: Optional[int] = None,
    python_exe: Optional[str] = None,
    env_overrides: Optional[Dict[str, str]] = None,
) -> IsolatedRunResult:
    """Run ``task`` in a fresh subprocess, return structured result.

    Blocks until child exits or the timeout fires. Caller is responsible
    for marshalling `task` into the same dict shape that `run_one_task`
    expects — typically the row from `agent_tasks` plus `inbox_message_id`.

    Args:
        task: agent_tasks row + sender metadata, JSON-serializable.
        timeout_s: hard wall-clock cap. On overrun the whole process group
            gets SIGTERM, a short grace, then SIGKILL, and is reaped.
        mem_limit_mb: RLIMIT_AS cap on POSIX. None = no cap.
        python_exe: override interpreter; defaults to `sys.executable`
            so the child uses the same venv as the parent.
        env_overrides: extra env vars merged into the child's env.
    """
    cmd = [
        python_exe or sys.executable,
        "-m",
        "app.run_isolated",
    ]

    env = os.environ.copy()
    # The child entry (`python -m app.run_isolated`) does NOT import
    # app.main, so the lifespan startup chain (DBOS.launch, workforce
    # scheduler, route mounts) never fires. We intentionally don't set
    # NOUS_ROLE in the child — there's nothing to gate.
    if env_overrides:
        env.update(env_overrides)

    mem_bytes = (mem_limit_mb * 1024 * 1024) if mem_limit_mb else None
    preexec = _build_preexec_fn(mem_bytes)

    task_id = str(task.get("id") or "")
    logger.info(
        f"[isolated_runner] spawn task_id={task_id} "
        f"timeout={timeout_s}s mem_cap={mem_limit_mb or 'none'}MB"
    )

    child = _spawn_and_wait(cmd, task, timeout_s, env, preexec, task_id)
    return _to_result(child, task_id, timeout_s)


@dataclass(frozen=True)
class _ChildOutcome:
    returncode: Optional[int]
    timed_out: bool
    stdout: str
    stderr: str


_GRACE_S = 2.0
_REAP_BOUND_S = 5.0


def _spawn_and_wait(
    cmd: list[str],
    task: Dict[str, Any],
    timeout_s: float,
    env: Dict[str, str],
    preexec,
    task_id: str,
) -> _ChildOutcome:
    """Blocking spawn in a NEW session (so the group kill reaches every
    descendant), feed the task on stdin, wait with a deadline. On timeout:
    group SIGTERM → grace → SIGKILL, then reap and keep the partial output."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        preexec_fn=preexec,  # ignored on Windows / when None
        start_new_session=True,
    )
    payload = json.dumps(task, ensure_ascii=False, default=str)
    try:
        out, err = proc.communicate(input=payload, timeout=timeout_s)
        return _ChildOutcome(proc.returncode, False, out or "", err or "")
    except subprocess.TimeoutExpired:
        kill_process_tree_sync(proc.pid, grace_seconds=_GRACE_S)
        try:
            out, err = proc.communicate(timeout=_REAP_BOUND_S)
        except subprocess.TimeoutExpired:
            logger.error(
                f"[isolated_runner] task_id={task_id} not reaped "
                f"{_REAP_BOUND_S}s after group kill; output dropped"
            )
            out, err = "", ""
        return _ChildOutcome(proc.returncode, True, out or "", err or "")


_SIGNAL_CODES = {
    signal.SIGKILL: ("oom_killed", "child killed by SIGKILL (likely OOM)"),
    signal.SIGSEGV: ("segfault", "child crashed with SIGSEGV (C extension bug)"),
    signal.SIGTERM: ("terminated", "child received SIGTERM during shutdown"),
}


def _parse_envelope(stdout: str) -> Optional[Dict[str, Any]]:
    """The envelope is the LAST stdout line; earlier output may be stray
    print()s. Parsed regardless of how the child ended — it may have written
    it before being killed."""
    if not stdout.strip():
        return None
    try:
        env = json.loads(stdout.strip().splitlines()[-1].strip())
    except json.JSONDecodeError:
        return None
    return env if isinstance(env, dict) else None


def _failed(common: Dict[str, Any], code: str, message: str) -> IsolatedRunResult:
    return IsolatedRunResult(
        status="failed", error_code=code, error_message=message, **common
    )


def _to_result(
    child: _ChildOutcome, task_id: str, timeout_s: float
) -> IsolatedRunResult:
    rc = child.returncode
    exit_code, sig = (None, -rc) if rc is not None and rc < 0 else (rc, None)
    stderr = child.stderr.strip()
    if stderr:
        # Surface child stderr at parent log level; truncate — full
        # tracebacks can be 5KB+ and bloat the worker log.
        logger.debug(
            f"[isolated_runner] task_id={task_id} child stderr "
            f"({len(stderr)} bytes): {stderr[:1500]}"
        )
    envelope = _parse_envelope(child.stdout)
    env = envelope or {}
    common: Dict[str, Any] = dict(
        task_id=env.get("task_id") or task_id or None,
        run_id=env.get("run_id"),
        exit_code=exit_code,
        signal=sig,
        timed_out=child.timed_out,
        idle_dispatch=env.get("idle_dispatch"),
    )
    if child.timed_out:
        logger.warning(
            f"[isolated_runner] task_id={task_id} timed out after {timeout_s}s "
            f"(exit_code={exit_code} signal={sig}); "
            f"partial_stdout={child.stdout.strip()[:200]!r}"
        )
        return _failed(
            common, "timeout", f"child exceeded {timeout_s}s wall-clock limit"
        )
    if envelope is not None and exit_code == 0:
        return IsolatedRunResult(
            status=str(env.get("status") or "unknown"),
            error_code=env.get("error_code"),
            error_message=env.get("error_message"),
            **common,
        )
    if sig in _SIGNAL_CODES:
        ec, msg = _SIGNAL_CODES[sig]
        logger.warning(
            f"[isolated_runner] task_id={task_id} killed by signal {sig} ({ec})"
        )
        return _failed(common, ec, msg)
    return _crashed(common, envelope, task_id, exit_code, sig, stderr)


def _crashed(
    common: Dict[str, Any],
    envelope: Optional[Dict[str, Any]],
    task_id: str,
    exit_code: Optional[int],
    sig: Optional[int],
    stderr: str,
) -> IsolatedRunResult:
    """Non-zero exit (or an unmapped signal): the envelope, if the child
    managed to write one, still says why."""
    how = f"exit {exit_code}" if sig is None else f"signal {sig}"
    if envelope is not None:
        return IsolatedRunResult(
            status=str(envelope.get("status") or "failed"),
            error_code=envelope.get("error_code") or "runtime_error",
            error_message=envelope.get("error_message")
            or f"child {how}, stderr: {stderr[:200]}",
            **common,
        )
    logger.error(
        f"[isolated_runner] task_id={task_id} {how} no envelope; "
        f"stderr={stderr[:300]!r}"
    )
    return _failed(
        common,
        "runtime_error",
        f"child {how} with no envelope; stderr: {stderr[:200]}",
    )


__all__ = ["IsolatedRunResult", "run_isolated"]
