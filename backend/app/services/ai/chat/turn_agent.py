"""Resolve the agent a session turn runs as — and refuse deleted ones (mig 501).

A session names its agent twice: ``agent_slug`` (what the turn has always
resolved by) and ``agent_id`` (the row it was bound to at creation, or at the
last issue reassignment). Agents are soft-deleted now, so three outcomes:

* the bound agent is deleted → 409 ``agent_deleted``. This holds even when
  the slug now resolves to a DIFFERENT live agent: slugs are freed on delete
  and can be reused, and a conversation must not quietly continue with an
  agent that merely shares the old one's name;
* the slug resolves to a live agent → that agent (override layers applied);
* nothing live and no tombstone → 404, as before.

Called before the user message is persisted, so a refused turn leaves no
unanswerable message in the thread.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException, status
from loguru import logger

AGENT_DELETED_CODE = "agent_deleted"


def agent_deleted_error(agent_slug: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": AGENT_DELETED_CODE,
            "message": (
                "The agent behind this conversation was deleted. The history "
                "stays readable; start a new conversation with another agent."
            ),
            "agent_slug": agent_slug,
        },
    )


def _is_deleted(row: Any) -> bool:
    return isinstance(row, dict) and bool(row.get("deleted_at"))


async def _bound_agent_is_deleted(agent_repo: Any, bound_id: Optional[str]) -> bool:
    if not bound_id:
        return False
    try:
        bound = await agent_repo.get_by_id(UUID(str(bound_id)))
    except ValueError:
        logger.warning(f"[turn_agent] session agent_id is not a uuid: {bound_id!r}")
        return False
    return _is_deleted(bound)


async def resolve_turn_agent(
    agent_repo: Any,
    *,
    session: Dict[str, Any],
    agent_slug: str,
    user_id: UUID,
) -> Dict[str, Any]:
    """Return the live agent record for this turn, or raise 409 / 404."""
    bound_id = session.get("agent_id")
    record = await agent_repo.get_by_slug(
        agent_slug,
        override_user_id=user_id,
        override_team_id=session.get("team_id"),
    )
    if record is not None and (not bound_id or str(record.get("id")) == str(bound_id)):
        return record
    # Either nothing live answers to the slug, or a different agent does.
    # Only now pay for the extra lookup of the row the session is bound to.
    if await _bound_agent_is_deleted(agent_repo, bound_id):
        raise agent_deleted_error(agent_slug)
    if record is not None:
        return record
    tomb = await agent_repo.get_by_slug(agent_slug, include_deleted=True)
    if _is_deleted(tomb):
        raise agent_deleted_error(agent_slug)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"agent slug not found: {agent_slug}",
    )
