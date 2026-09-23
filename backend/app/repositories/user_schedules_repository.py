"""``user_schedules`` writes that are about an ISSUE rather than one row.

Defect B (2026-09-23 prod S2): ``ScheduleWakeup`` caps wake-ups per RUN, so
every woken run gets a fresh budget and an agent's chain outlives the work.
``disarm_agent_wakeups`` stops the chain at the edges where the work is over:
the agent declared ``completed`` / was capped on ``continue``
(``issue_lifecycle.route_finish_outcome``) and the issue was cancelled
(``cancel_live_work.stop_live_work_for_cancel``). Pause does not disarm
(ruling 2): a paused issue already takes its wake-ups into the inbox.

Only rows the AGENT armed (``payload.created_by == "agent"``, written by the
tool) are touched. A person's wake-up from the ⏰ popover — or a legacy row
with no ``created_by`` — keeps firing until the issue is done/cancelled, which
is all spec §5 promised.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update

from app.db.session import write_scope
from app.models import UserSchedules

#: Written by a disarm on completion and by the firing guard: the issue is
#: waiting on a person (in_review / needs_followup), so an agent wake-up would
#: only start a billed run whose status write is skipped.
ISSUE_NOT_ACTIVE = "issue_not_active"
#: The existing reason for "the issue is done/cancelled" (scheduled_master).
ISSUE_TERMINAL = "issue_terminal"

WAKEUP_TASK_TYPE = "issue_wakeup"
AGENT_CREATOR = "agent"


async def disarm_agent_wakeups(issue_id: int, *, reason: str) -> int:
    """Disable every still-armed agent wake-up on ``issue_id``; return how many.

    Same disable shape as ``scheduled_master._disable_schedule``: ``paused_at``
    is written alongside the reason, because the Schedules block renders a
    reason only behind it. A row that already stopped (fired, disarmed) is not
    matched, so it keeps the reason it stopped with.

    ``payload->>'issue_id'`` is compared as text: the tool stores the id as a
    JSON number and ``->>`` renders it without quotes either way."""
    async with write_scope() as session:
        result = await session.execute(
            update(UserSchedules)
            .where(UserSchedules.task_type == WAKEUP_TASK_TYPE)
            .where(UserSchedules.enabled.is_(True))
            .where(UserSchedules.payload["issue_id"].astext == str(int(issue_id)))
            .where(UserSchedules.payload["created_by"].astext == AGENT_CREATOR)
            .values(
                enabled=False,
                pause_reason=reason,
                paused_at=datetime.now(timezone.utc),
            )
        )
    return int(result.rowcount or 0)


__all__ = [
    "AGENT_CREATOR",
    "ISSUE_NOT_ACTIVE",
    "ISSUE_TERMINAL",
    "disarm_agent_wakeups",
]
