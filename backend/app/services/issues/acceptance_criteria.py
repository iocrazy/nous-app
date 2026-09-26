"""Acceptance criteria on an issue — the agent-side writes (509).

The human path is PATCH /issues/{id} (router stamps source='user'). This
module serves the SetAcceptanceCriteria tool: read the current pair, write an
agent proposal, and leave a visible thread row so a person can see (and edit)
what the agent decided to be judged against.

Thread row shape (deviation 2 of the plan): ``issue_messages.kind='comment'``
with ``author_agent_id`` — the ``system_status`` kind requires a status
transition — and ``meta.kind='criteria_proposed'``.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

CRITERIA_PROPOSED_META_KIND = "criteria_proposed"


class CriteriaLockedError(RuntimeError):
    """The UPDATE matched no row: a person's criteria landed between the
    tool's read and its write (or the issue is gone). The tool answers
    ``criteria_locked``."""


async def load_acceptance_criteria(
    issue_id: int,
) -> tuple[Optional[str], Optional[str]]:
    """``(criteria, source)`` for the issue; ``(None, None)`` when unset."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Issues

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    Issues.acceptance_criteria, Issues.acceptance_criteria_source
                ).where(Issues.id == int(issue_id))
            )
        ).first()
    if not row:
        return None, None
    criteria = (row[0] or "").strip() or None
    return criteria, (row[1] if criteria else None)


async def write_agent_criteria(
    issue_id: int, criteria: str, *, agent_id: Optional[str]
) -> None:
    """Write the agent's proposal (source='agent') and its thread row.

    The column write raises on failure (the tool turns it into a typed
    error); the thread row is best-effort and logged — it is a record, not
    the criteria."""
    from sqlalchemy import insert, update

    from app.db.session import write_scope
    from app.models import IssueMessages, Issues

    async with write_scope() as session:
        result = await session.execute(
            update(Issues)
            .where(Issues.id == int(issue_id))
            # A PATCH landing between the tool's read and this write must not
            # be overwritten: a user-owned row is never matched.
            .where(Issues.acceptance_criteria_source.is_distinct_from("user"))
            .values(acceptance_criteria=criteria, acceptance_criteria_source="agent")
        )
    if not (getattr(result, "rowcount", 0) or 0):
        raise CriteriaLockedError(
            f"issue {issue_id}: criteria are user-owned or the issue is gone"
        )
    # kind='comment' needs an author (issue_messages_author_chk); the tool's
    # factory has no user id, so without an agent id the row is skipped.
    if not agent_id:
        logger.warning(
            f"[acceptance_criteria] issue {issue_id}: no agent id; skipping thread row"
        )
        return
    try:
        async with write_scope() as session:
            await session.execute(
                insert(IssueMessages).values(
                    issue_id=int(issue_id),
                    kind="comment",
                    author_agent_id=agent_id,
                    body=criteria,
                    meta={"kind": CRITERIA_PROPOSED_META_KIND, "source": "agent"},
                )
            )
    except Exception as exc:  # noqa: BLE001 — logged, not swallowed
        logger.opt(exception=True).error(
            f"[acceptance_criteria] issue {issue_id}: criteria_proposed row failed: {exc}"
        )


def criteria_section(criteria: Optional[str], source: Optional[str]) -> Optional[str]:
    """The user-message section the executor appends after Details."""
    from app.boundary.frame_markers import escape_frame_body

    text = (criteria or "").strip()
    if not text:
        return None
    return (
        f"\nAcceptance criteria (source={source or 'user'}):\n{escape_frame_body(text)}"
    )


__all__: list[str] = [
    "CRITERIA_PROPOSED_META_KIND",
    "CriteriaLockedError",
    "criteria_section",
    "load_acceptance_criteria",
    "write_agent_criteria",
]
