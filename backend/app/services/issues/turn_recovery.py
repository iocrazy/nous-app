"""Bound how many times DBOS recovery may re-execute one issue turn step.

``run_issue_agent_step`` has ``retries_allowed=False``, but recovery still
re-executes the step that was running when the worker died — and every
re-execution is a new billed run. Before this the only bound was DBOS
``recovery_attempts`` (prod: one execute_issue workflow reached 6), and the
count lived nowhere an issue reader could see.

Two turn steps call ``enforce_recovery_limit`` first thing inside the step:
``run_issue_agent`` (via ``run_issue_agent_step``, the dispatch turn) and
``run_issue_reply_step`` (the reply turn, fh3 T2 — reached from both
``respond_to_issue_reply`` and ``execute_issue``'s wait loop). Each also
threads the same key into ``run_session_turn`` so a re-execution reuses the
stamped user message and links its run to the killed one.

The limit counts executions per ``<workflow_id>:<step_id>`` in
``issues.execution_state.step_attempts`` (atomic jsonb increment) and raises
``IssueTurnRecoveryLimitExceeded`` past ``ISSUE_TURN_MAX_RECOVERIES``. The
exception propagates out of the step; the callers' existing ``except`` blocks
route it (DBOS failure must raise, never return a dict): ``execute_issue``
parks the issue ``blocked`` / ``execute_issue_failed``; a resuming reply parks
it ``blocked`` / ``issue_reply_resume_failed``; a plain reply never touched
status, so its workflow just ends in ERROR.

Those ``except`` blocks write their own error codes — changing them would
change the workflow's source and so its DBOS app-version hash, which
orphans every workflow in flight at the deploy. The typed code therefore
rides in the message: ``error_message`` starts with
``[issue_turn_recovery_limit]``, and ``step_attempts`` stays on the row.

"Recovery" means any re-execution of the same step under the same workflow
id: automatic DBOS recovery after a worker restart AND an admin's manual
``resume_workflow`` (``workflows_router``). Both count toward the limit.

Nothing here is a DBOS step (no new step in any workflow body).
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

# First execution is attempt 1; attempt 1 + N is the N-th recovery.
ISSUE_TURN_MAX_RECOVERIES = 2
RECOVERY_LIMIT_ERROR_CODE = "issue_turn_recovery_limit"


class IssueTurnRecoveryLimitExceeded(RuntimeError):
    """The same turn step was re-executed by recovery more than allowed."""

    error_code = RECOVERY_LIMIT_ERROR_CODE

    def __init__(self, issue_id: int, step_key: str, attempts: int) -> None:
        # Positional and all in ``self.args``: DBOS pickles a step's exception
        # and rebuilds it on replay via ``Exception.__reduce__`` =
        # ``(cls, self.args)``. A keyword-only __init__ made that rebuild raise
        # TypeError, and the typed message prefix was lost exactly when a
        # worker died between the step raising and the body parking the issue.
        super().__init__(issue_id, step_key, attempts)
        self.issue_id = issue_id
        self.step_key = step_key
        self.attempts = attempts

    def __str__(self) -> str:
        return (
            f"[{RECOVERY_LIMIT_ERROR_CODE}] issue {self.issue_id} turn step "
            f"{self.step_key} was re-executed by worker recovery "
            f"{self.attempts - 1} times (limit {ISSUE_TURN_MAX_RECOVERIES}); not "
            f"starting another billed run"
        )


def current_dbos_step_key() -> Optional[str]:
    """``<workflow_id>:<step_id>`` of the DBOS step executing right now —
    identical on every re-execution of that step. None outside a step (tests,
    scripts, workflow body), which keeps today's behaviour."""
    from dbos import DBOS

    workflow_id, step_id = DBOS.workflow_id, DBOS.step_id
    if not workflow_id or step_id is None:
        return None
    return f"{workflow_id}:{step_id}"


async def record_step_attempt(issue_id: int, step_key: str) -> Optional[int]:
    """Increment and return this step's execution count; None if it could not
    be recorded (logged — the counter is a brake, not a gate)."""
    from app.services.issues import execution_state

    try:
        return await execution_state.increment_step_attempt(issue_id, step_key)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.error(
            f"[turn_recovery] issue={issue_id} step={step_key}: attempt counter "
            f"not written ({exc!r}); DBOS recovery_attempts is the only bound"
        )
        return None


async def enforce_recovery_limit(issue_id: int, step_key: str) -> Optional[int]:
    """Count this execution; raise when it is past the recovery limit."""
    attempts = await record_step_attempt(issue_id, step_key)
    if attempts is None:
        return None
    if attempts > 1:
        logger.warning(
            f"[turn_recovery] issue={issue_id} step={step_key} re-executed by "
            f"recovery (attempt {attempts})"
        )
    if attempts > 1 + ISSUE_TURN_MAX_RECOVERIES:
        raise IssueTurnRecoveryLimitExceeded(issue_id, step_key, attempts)
    return attempts


__all__ = [
    "ISSUE_TURN_MAX_RECOVERIES",
    "IssueTurnRecoveryLimitExceeded",
    "RECOVERY_LIMIT_ERROR_CODE",
    "current_dbos_step_key",
    "enforce_recovery_limit",
    "record_step_attempt",
]
