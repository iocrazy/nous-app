"""run_process — the one async way to run a child process to completion.

Every migrated spawn site (yt-dlp, ffmpeg, dreamina, gpt-image-2-skill)
calls this instead of ``asyncio.create_subprocess_exec``; a source scan
(``tests/agent_framework/test_subprocess_registration_guard.py``) keeps it
that way under ``workflows/`` and ``services/media|workforce/``.

What it guarantees:

* **Scrubbed env + own process group** — ``**safe_popen_kwargs(...)`` is
  splatted inline in the spawn call (the scrub guard checks the call node).
* **Orthogonal result** — returns a ``ProcessResult``; ``timed_out``,
  ``cancelled``, ``exit_code`` and ``signal`` are independent fields. It
  NEVER raises on timeout or cancel — callers map the result onto their own
  typed errors (``JimengCliError("timeout")`` etc. are unchanged). It raises
  only when the child cannot be spawned (``FileNotFoundError`` / ``OSError``)
  or when the awaiting task itself is cancelled (after killing the child).
* **Bounded reads** — reading stdout/stderr happens INSIDE the deadline
  (fh4 H6: reading to EOF used to happen outside it, so a hung child with
  open pipes blocked forever).
* **Dispose reaches quiescence** — on timeout / cancel the whole process
  group gets SIGTERM → grace → SIGKILL (``kill_process_tree``) and we then
  await the reap, bounded. If the awaiting task is cancelled, the child is
  killed before the ``CancelledError`` propagates.
* **Cancel reaches the worker** — the pid is registered in the per-process
  ``subprocess_registry`` (teardown / atexit / same-process cancel). A cancel
  issued in ANOTHER process (Task Center in ``nous-backend``, the child in
  ``nous-worker``) is seen by polling ``task_tracking`` every
  ``cancel_poll_s`` seconds (fh4 ruling 1): both cancel paths leave the row at
  ``phase='cancelled'`` / ``status='cancelled'`` — the Task Center writes both
  directly, flow cascade cancel via DBOS ``CANCELLED`` →
  ``mirror_dbos_lifecycle_to_tracking``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Awaitable, Callable, Iterable

from loguru import logger

from app.agent_framework.kill_tree import KillOutcome, kill_process_tree
from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.agent_framework.process_result import ProcessResult, split_returncode
from app.agent_framework.subprocess_registry import (
    UNSCOPED,
    register_subprocess,
    unregister_subprocess,
)

#: How often the wait loop asks ``task_tracking`` whether the workflow was
#: cancelled (only when a ``workflow_id`` is given).
CANCEL_POLL_S = 2.0
#: After a kill, how long we wait for the pipes to drain and the child to be
#: reaped before giving up on its output (logged at ERROR).
REAP_BOUND_S = 5.0

LineCallback = Callable[[str], "Awaitable[None] | None"]

_CANCELLED_VALUES = ("cancelled",)


def current_workflow_id() -> str | None:
    """The DBOS workflow we are running in, or ``None`` outside one.

    Tolerant on purpose: this only decides whether the child is registered
    and cancel-polled; a missing DBOS runtime (unit tests, API routes) must
    never break the spawn itself.
    """
    try:
        from dbos import DBOS

        return DBOS.workflow_id or None
    except Exception:  # noqa: BLE001 — no DBOS runtime
        return None


async def is_workflow_cancelled(workflow_id: str) -> bool:
    """Has ``workflow_id`` been cancelled, per ``task_tracking``?

    Reads ``phase`` and ``status`` of the row keyed by ``dbos_workflow_id``;
    either being ``'cancelled'`` counts (both writers set both, but a
    half-applied write must not keep a child alive). Fails OPEN: a DB error
    is logged and reads as "not cancelled" — the deadline still bounds the
    child, whereas killing a healthy download on a DB hiccup would not be
    recoverable.
    """
    from sqlalchemy import select

    from app.db import session as db_session
    from app.models import TaskTracking

    try:
        async with db_session.read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(TaskTracking.phase, TaskTracking.status)
                        .where(TaskTracking.dbos_workflow_id == workflow_id)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:  # noqa: BLE001 — fail open, see docstring
        logger.warning(f"[run_process] cancel poll failed for wf={workflow_id}: {exc}")
        return False
    if not row:
        return False
    return (
        row.get("phase") in _CANCELLED_VALUES or row.get("status") in _CANCELLED_VALUES
    )


class _Buffers:
    """Output collected so far — survives a timeout (partial output)."""

    def __init__(self) -> None:
        self.stdout = bytearray()
        self.stderr = bytearray()


async def _deliver(cb: LineCallback, line: str) -> None:
    try:
        res = cb(line)
        if inspect.isawaitable(res):
            await res
    except Exception as exc:  # noqa: BLE001 — a bad listener never kills the run
        logger.error(f"[run_process] stdout line callback raised: {exc}")


async def _pump(stream, sink: bytearray, on_line: LineCallback | None) -> None:
    if stream is None:
        return
    if on_line is None:
        while chunk := await stream.read(65536):
            sink.extend(chunk)
        return
    while line := await stream.readline():
        sink.extend(line)
        await _deliver(on_line, line.decode("utf-8", errors="replace").rstrip("\r\n"))


async def _communicate(
    proc, stdin: bytes | None, bufs: _Buffers, on_line: LineCallback | None
) -> None:
    """Feed stdin, drain both pipes, wait for exit — as ONE awaitable, so the
    deadline covers the reads (H6). After a kill the pipes hit EOF, so the
    output gathered so far still lands in ``bufs``."""
    if on_line is None:
        out, err = await (
            proc.communicate(stdin) if stdin is not None else proc.communicate()
        )
        bufs.stdout.extend(out or b"")
        bufs.stderr.extend(err or b"")
        return
    if stdin is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # child exited without reading; its status tells the story
        finally:
            proc.stdin.close()
    pumps = [
        asyncio.ensure_future(_pump(proc.stdout, bufs.stdout, on_line)),
        asyncio.ensure_future(_pump(proc.stderr, bufs.stderr, None)),
    ]
    try:
        await asyncio.gather(*pumps)
    finally:
        # A failed reader (e.g. a line over the 64 KiB StreamReader limit)
        # must not leave its sibling pump reading a pipe nobody drains.
        for t in pumps:
            t.cancel()
    await proc.wait()


async def _await_stop(
    io: asyncio.Task,
    deadline: float,
    workflow_id: str | None,
    cancel_poll_s: float,
) -> str:
    """Wait until the child finishes, the deadline passes, or the workflow is
    cancelled. Returns ``"done" | "timeout" | "cancelled"``."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        slice_s = min(remaining, cancel_poll_s) if workflow_id else remaining
        done, _ = await asyncio.wait({io}, timeout=slice_s)
        if io in done:
            return "done"
        if workflow_id and await is_workflow_cancelled(workflow_id):
            return "cancelled"


async def _stop(proc, grace_s: float) -> KillOutcome | None:
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        return await kill_process_tree(pid, grace_seconds=grace_s)
    kill = getattr(proc, "kill", None)  # doubles without a pid
    if callable(kill):
        kill()
    return None


async def _settle(io: asyncio.Task, argv0: str) -> None:
    """After a kill: let the pipes drain and the reap land, bounded."""
    done, _ = await asyncio.wait({io}, timeout=REAP_BOUND_S)
    if io in done:
        if not io.cancelled() and io.exception() is not None:
            logger.warning(f"[run_process] {argv0} io after kill: {io.exception()}")
        return
    io.cancel()
    logger.error(
        f"[run_process] {argv0} not reaped {REAP_BOUND_S}s after kill "
        "(a descendant outside the group holds the pipes?) — output truncated"
    )


async def _spawn(argv: list[str], stdin: bytes | None, env_keep, env_extra):
    # The splat stays inline in the spawn call: the scrub guard
    # (tests/agent_framework/test_scrubbed_env.py) checks the call node.
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=(
            asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **safe_popen_kwargs(env_keep=env_keep, env_extra=env_extra),
    )


def _report(result: ProcessResult, argv0: str, pid, workflow_id, timeout_s) -> None:
    """Every flag in every line — never one folded into another."""
    if result.cancelled:
        logger.info(
            f"[run_process] Cancel killed 1 subprocess(es) for {workflow_id}: "
            f"{argv0} pid={pid} {result.describe()}"
        )
    elif result.timed_out:
        logger.warning(
            f"[run_process] {argv0} timed out after {timeout_s}s: {result.describe()}"
        )
    if result.exit_code is None and result.signal is None:
        logger.error(
            f"[run_process] {argv0} pid={pid} was never reaped: {result.describe()}"
        )


async def run_process(
    argv: list[str],
    *,
    timeout_s: float,
    grace_s: float = 2.0,
    stdin: bytes | None = None,
    workflow_id: str | None = None,
    env_keep: Iterable[str] = (),
    env_extra: dict[str, str] | None = None,
    cancel_poll_s: float = CANCEL_POLL_S,
    on_stdout_line: LineCallback | None = None,
) -> ProcessResult:
    """Run ``argv`` to completion (or deadline / cancel). See module docstring.

    ``on_stdout_line`` streams decoded stdout lines (progress parsing) while
    still collecting the raw bytes into the result.
    """
    t0 = time.monotonic()
    argv0 = str(argv[0]) if argv else "?"
    if workflow_id and await is_workflow_cancelled(workflow_id):
        # A retry of an already-cancelled workflow must not relaunch the
        # child for another poll interval. Nothing ran: no exit status.
        logger.info(f"[run_process] {argv0} not started: wf={workflow_id} cancelled")
        return ProcessResult(None, None, False, b"", b"", 0.0, cancelled=True)
    proc = await _spawn(argv, stdin, env_keep, env_extra)
    pid = getattr(proc, "pid", None)
    pid = pid if isinstance(pid, int) and pid > 0 else 0
    reg_key = workflow_id or UNSCOPED
    register_subprocess(reg_key, pid)  # before any wait: cancel can find it
    bufs = _Buffers()
    io = asyncio.ensure_future(_communicate(proc, stdin, bufs, on_stdout_line))
    try:
        stop = await _await_stop(io, t0 + timeout_s, workflow_id, cancel_poll_s)
        if stop != "done":
            await _stop(proc, grace_s)
            await _settle(io, argv0)
        elif io.exception() is not None:
            # The reader broke while the child may still be running: dispose
            # exactly as on timeout (kill the group, bounded reap) BEFORE the
            # finally below unregisters it.
            logger.warning(f"[run_process] {argv0} io error: {io.exception()}")
            if getattr(proc, "returncode", None) is None:
                await _stop(proc, grace_s)
                await _settle(io, argv0)
    except BaseException:
        # The awaiting task was cancelled (or something below us broke):
        # reach quiescence first, then propagate the ORIGINAL exception —
        # a failure during this cleanup is logged, never allowed to mask it.
        try:
            await _stop(proc, grace_s)
            await _settle(io, argv0)
        except BaseException as cleanup_exc:  # noqa: BLE001
            logger.error(
                f"[run_process] {argv0} cleanup after error failed: {cleanup_exc!r}"
            )
        raise
    finally:
        still_mine = unregister_subprocess(reg_key, pid)
    # Detached by someone else (same-process registry cancel) = cancelled.
    detached = bool(pid) and not still_mine
    exit_code, sig = split_returncode(getattr(proc, "returncode", None))
    result = ProcessResult(
        exit_code=exit_code,
        signal=sig,
        timed_out=stop == "timeout",
        stdout=bytes(bufs.stdout),
        stderr=bytes(bufs.stderr),
        duration_s=time.monotonic() - t0,
        cancelled=stop == "cancelled" or (stop == "done" and detached),
    )
    _report(result, argv0, pid, workflow_id, timeout_s)
    return result


__all__ = [
    "CANCEL_POLL_S",
    "current_workflow_id",
    "is_workflow_cancelled",
    "run_process",
]
