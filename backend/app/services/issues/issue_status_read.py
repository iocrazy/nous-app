"""Plain, best-effort read of ``issues.status`` — the authoritative cancel
signal (hotfix-2 ruling 1; ``agent_runs.cancel_requested`` only speeds it up).

Deliberately NOT a DBOS step: it is called from inside a running step (the
issue turn's pre-run gate and the per-step ``IssueStatusGate``) and from the
workflow body, where a new recorded step would shift the step sequence of
workflows recovered across a deploy.

Best-effort on purpose: a failed read logs at ERROR and returns ``None``,
which every caller treats as "not preempted". A DB hiccup must never block a
turn — the next gate (the following step, the workflow's final re-read,
``set_status``'s terminal guard) is still behind it.
"""

from __future__ import annotations

from loguru import logger

from app.services.issues.cancel_live_work import TERMINAL_STATUSES

# The statuses that mean "stop working this issue". Same set as
# ``issue_lifecycle.PREEMPT_STATUSES`` (a test pins the equality); taken from
# the DBOS-free module so the runner can import it without the workflow one.
PREEMPT_STATUSES = TERMINAL_STATUSES


async def read_issue_status(issue_id: int, *, purpose: str) -> str | None:
    """Current ``issues.status`` of ``issue_id``, or ``None`` when unreadable.

    ``purpose`` names the caller in the ERROR line so a failing read says
    which gate degraded.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Issues

    try:
        async with read_scope() as session:
            return (
                await session.execute(
                    select(Issues.status).where(Issues.id == issue_id)
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — logged; the caller carries on
        logger.error(
            f"[issue_status] issue {issue_id}: status read for {purpose} failed "
            f"({exc!r}); treating the issue as not preempted"
        )
        return None


__all__ = ["PREEMPT_STATUSES", "read_issue_status"]
