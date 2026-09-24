"""``user_schedules`` reads and writes that are about an ISSUE rather than one row.

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

``count_agent_wakeups_since_human`` (FH2 T2) is the other cut: at most
``MAX_AGENT_WAKEUPS_PER_ISSUE`` agent wake-ups in a row on one issue until a
person speaks, because the per-run budget resets with every woken run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update

from app.db.session import read_scope, write_scope
from app.models import Issues, Messages, UserSchedules

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


def count_agent_wakeups_since_human_stmt(issue_id: int):
    """``SELECT count(*)`` of the agent-armed wake-ups on ``issue_id`` created
    after the last time a PERSON spoke on the issue (FH2 T2, per-issue cap).

    Where a person speaks: on an agent-assigned issue — the only kind a
    wake-up is armed on — a comment is a user-role message in the issue's
    session conversation (``issues.ai_session_id``). ``issue_messages`` holds
    only the legacy no-agent comments and the wake-up MIRROR rows, whose
    ``author_user_id`` is the owner (prod 2026-09-23: the only rows there with
    an author user are 6 wake-up mirrors), so anchoring on it would reset the
    count on every fired wake-up and the cap would never trip.

    Not a person, even though the role is ``user``: a delivery that carries
    provenance (``body.meta.source`` — a wake-up, a sub-issue barrier) and the
    continuation nudge the dispatch loop sends itself. With no human message
    at all the anchor is ``issues.created_at``; a missing issue has no anchor
    and counts nothing."""
    from app.services.issues.issue_agent_executor import CONTINUATION_NUDGE

    last_human = (
        select(func.max(Messages.created_at))
        .where(Messages.conversation_id == Issues.ai_session_id)
        .where(Messages.sender_type == "user")
        .where(Messages.body["meta"]["source"].is_(None))
        .where(func.coalesce(Messages.body["text"].astext, "") != CONTINUATION_NUDGE)
        .correlate(Issues)
        .scalar_subquery()
    )
    since = (
        select(func.coalesce(last_human, Issues.created_at))
        .where(Issues.id == int(issue_id))
        .scalar_subquery()
    )
    return (
        select(func.count())
        .select_from(UserSchedules)
        .where(UserSchedules.task_type == WAKEUP_TASK_TYPE)
        .where(UserSchedules.payload["issue_id"].astext == str(int(issue_id)))
        .where(UserSchedules.payload["created_by"].astext == AGENT_CREATOR)
        .where(UserSchedules.created_at > since)
    )


async def count_agent_wakeups_since_human(issue_id: int) -> int:
    """Run ``count_agent_wakeups_since_human_stmt``. Burned and disarmed rows
    still count: the cap is on how often the agent ARMED one, like the
    per-run budget."""
    async with read_scope() as session:
        return int(
            (
                await session.execute(count_agent_wakeups_since_human_stmt(issue_id))
            ).scalar_one()
        )


__all__ = [
    "AGENT_CREATOR",
    "ISSUE_NOT_ACTIVE",
    "ISSUE_TERMINAL",
    "count_agent_wakeups_since_human",
    "count_agent_wakeups_since_human_stmt",
    "disarm_agent_wakeups",
]
