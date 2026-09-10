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

Exit code mapping
-----------------
The OS / shell injects specific codes that we treat as known failure
modes (rather than generic runtime_error):

    0     — child wrote envelope; honor `status` field as-is.
    1     — child crashed with uncaught exception (envelope present).
    124   — bash convention for `timeout` command's SIGKILL victim;
            we use it ourselves when our wall-clock killer fires.
    137   — 128 + 9 (SIGKILL) — typically OOM-killer or our timeout.
    139   — 128 + 11 (SIGSEGV) — C extension crashed.
    143   — 128 + 15 (SIGTERM) — graceful shutdown signal.

Anything else gets bucketed as `runtime_error` with the exit code in
the message so the operator can grep logs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


@dataclass(frozen=True)
class IsolatedRunResult:
    """Envelope returned to callers regardless of how the run terminated."""

    status: str  # "done" | "failed" | "skipped"
    task_id: Optional[str]
    run_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    exit_code: int  # the actual OS exit code, for telemetry
    # The wake ORDER a background sub-agent's branch produced, carried through
    # the wire so subprocess mode delivers what in-process mode delivers. A
    # key dropped here is not an error anywhere — it is a wake-up that simply
    # never happens, in exactly one deployment mode.
    idle_dispatch: Optional[Dict[str, Any]] = None

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
        timeout_s: hard wall-clock cap. SIGKILL on overrun (no graceful).
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
    # MEDIAHUB_ROLE in the child — there's nothing to gate.
    if env_overrides:
        env.update(env_overrides)

    mem_bytes = (mem_limit_mb * 1024 * 1024) if mem_limit_mb else None
    preexec = _build_preexec_fn(mem_bytes)

    task_id = str(task.get("id") or "")
    logger.info(
        f"[isolated_runner] spawn task_id={task_id} "
        f"timeout={timeout_s}s mem_cap={mem_limit_mb or 'none'}MB"
    )

    try:
        completed = subprocess.run(
            cmd,
            input=json.dumps(task, ensure_ascii=False, default=str),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            preexec_fn=preexec,  # ignored on Windows / when None
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run does cleanup on TimeoutExpired (sends SIGKILL,
        # waits) before raising. The decoded stdout is in exc.stdout.
        partial_stdout = (
            (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        )
        logger.warning(
            f"[isolated_runner] task_id={task_id} timed out after {timeout_s}s; "
            f"partial_stdout={partial_stdout[:200]!r}"
        )
        return IsolatedRunResult(
            status="failed",
            task_id=task_id or None,
            run_id=None,
            error_code="timeout",
            error_message=f"child exceeded {timeout_s}s wall-clock limit",
            exit_code=124,
        )

    rc = completed.returncode
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    # Surface child stderr at parent log level so the parent's log file
    # captures everything in one place. Truncate aggressively — full
    # tracebacks can be 5KB+ and bloat the worker log.
    if stderr:
        logger.debug(
            f"[isolated_runner] task_id={task_id} child stderr "
            f"({len(stderr)} bytes): {stderr[:1500]}"
        )

    # Try to parse the envelope from stdout regardless of rc — the child
    # might have written it before being killed (e.g. SIGTERM during
    # shutdown), and it's the most reliable source of truth.
    envelope: Optional[Dict[str, Any]] = None
    if stdout:
        # Take the LAST line — earlier output may be unrelated print()s.
        # Our protocol is one envelope on the final line.
        last_line = stdout.splitlines()[-1].strip()
        try:
            envelope = json.loads(last_line)
        except json.JSONDecodeError:
            envelope = None

    if envelope is not None and rc == 0:
        return IsolatedRunResult(
            status=str(envelope.get("status") or "unknown"),
            task_id=envelope.get("task_id") or task_id or None,
            run_id=envelope.get("run_id"),
            error_code=envelope.get("error_code"),
            error_message=envelope.get("error_message"),
            exit_code=rc,
            idle_dispatch=envelope.get("idle_dispatch"),
        )

    # Non-zero rc — map known signal exits to friendly error codes so
    # the operator doesn't have to memorize POSIX exit conventions.
    code_map = {
        137: ("oom_killed", "child killed by SIGKILL (likely OOM)"),
        139: ("segfault", "child crashed with SIGSEGV (C extension bug)"),
        143: ("terminated", "child received SIGTERM during shutdown"),
        124: ("timeout", "child killed for exceeding wall-clock limit"),
    }
    if rc in code_map:
        ec, msg = code_map[rc]
        logger.warning(f"[isolated_runner] task_id={task_id} terminated rc={rc} ({ec})")
        return IsolatedRunResult(
            status="failed",
            task_id=task_id or None,
            run_id=envelope.get("run_id") if envelope else None,
            error_code=ec,
            error_message=msg,
            exit_code=rc,
        )

    # Generic non-zero — child crashed but envelope may still tell us why.
    if envelope is not None:
        return IsolatedRunResult(
            status=str(envelope.get("status") or "failed"),
            task_id=envelope.get("task_id") or task_id or None,
            run_id=envelope.get("run_id"),
            error_code=envelope.get("error_code") or "runtime_error",
            error_message=envelope.get("error_message")
            or f"child exit {rc}, stderr: {stderr[:200]}",
            exit_code=rc,
            idle_dispatch=envelope.get("idle_dispatch"),
        )

    logger.error(
        f"[isolated_runner] task_id={task_id} rc={rc} no envelope; "
        f"stderr={stderr[:300]!r}"
    )
    return IsolatedRunResult(
        status="failed",
        task_id=task_id or None,
        run_id=None,
        error_code="runtime_error",
        error_message=f"child exit {rc} with no envelope; stderr: {stderr[:200]}",
        exit_code=rc,
    )


__all__ = ["IsolatedRunResult", "run_isolated"]
