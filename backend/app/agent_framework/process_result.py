"""ProcessResult — how one child process ended, every outcome reported apart.

CLAUDE.md 防御模式「正交的结果各自独立上报」: a run can be several things at
once. A child can be *both* timed out *and* exit 0 (it trapped our SIGTERM and
exited cleanly), so the flags below are independent fields, never one folded
into another:

``timed_out``
    OUR deadline fired and we started killing the child. Set by the wrapper
    (``run_process``), never inferred from the exit status.
``cancelled``
    The owning workflow was cancelled while the child ran — either the wait
    loop saw ``task_tracking`` flip to cancelled, or an in-process
    ``cancel_workflow_subprocesses`` detached and killed it. Independent of
    ``timed_out``: a cancel arriving after the deadline fired leaves both set.
``exit_code`` / ``signal``
    From the ACTUAL reaped status. ``returncode >= 0`` → ``exit_code``;
    ``returncode < 0`` → ``signal = -returncode`` and ``exit_code = None``.
    A literal ``sys.exit(137)`` is ``exit_code=137, signal=None`` — never
    misread as a SIGKILL. Both are ``None`` only when the child could not be
    reaped within the bound (logged at ERROR by the runner).

The headline case: a child that traps SIGTERM and exits 0 on our timeout is
``timed_out=True, exit_code=0, signal=None`` — and ``ok`` is False, so no
caller can read a cut-short run as a clean success.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of one ``run_process`` call. See module docstring for the
    orthogonality rule."""

    exit_code: int | None
    signal: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    duration_s: float
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """Clean success: exited 0, and neither our deadline nor a cancel
        cut it short."""
        return self.exit_code == 0 and not self.timed_out and not self.cancelled

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace") if self.stdout else ""

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace") if self.stderr else ""

    def describe(self) -> str:
        """One-line summary for logs/errors — every flag, never just one."""
        return (
            f"exit_code={self.exit_code} signal={self.signal} "
            f"timed_out={self.timed_out} cancelled={self.cancelled} "
            f"duration={self.duration_s:.1f}s"
        )


def split_returncode(returncode: int | None) -> tuple[int | None, int | None]:
    """``(exit_code, signal)`` from a POSIX ``returncode`` (negative = killed
    by that signal, the asyncio / subprocess convention)."""
    if returncode is None:
        return None, None
    if returncode < 0:
        return None, -returncode
    return returncode, None


__all__ = ["ProcessResult", "split_returncode"]
