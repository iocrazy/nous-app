"""Handoff note written when an issue's assignee agent changes (FH2 T5).

The session itself is re-bound to the new assignee lazily, at the start of the
next turn (``issue_session._rebind_to_assignee``). This module only leaves the
visible trace: one user-role message on the issue's session whose row sender
is the human who reassigned it. It lands where both readers look —

* the issue thread UI reads the session (``issue_messages`` is the legacy path
  for session-less issues only), where a user-role row renders as a
  ``comment``. The thread mapper attributes EVERY user-role row to the session
  owner (the issue creator), not to the row's sender — so the body names the
  reassigner itself ("… by <username>"), or a teammate's reassignment would
  read as the creator's;
* the new agent replays the session history on its first turn, so it sees why
  a conversation it did not start is now its own.

A ``system`` role row was rejected on purpose: the thread maps it to
``system_status`` and renders it as a status transition ("STATUS →"), which a
reassignment is not.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

# Agent and user names are user-controlled text that ends up in model history.
REASSIGN_NAME_MAX = 80
UNKNOWN_ACTOR = "a teammate"


def _assignee(row: dict[str, Any]) -> Optional[str]:
    raw = row.get("assignee_agent_id")
    return str(raw) if raw else None


def _display_name(agent: Optional[dict[str, Any]], fallback: str) -> str:
    """One line, bounded: a crafted name cannot open a paragraph of its own."""
    raw = (agent or {}).get("name") or (agent or {}).get("slug") or fallback
    return _flatten(raw) or fallback


def _flatten(raw: Any) -> str:
    return " ".join(str(raw).split())[:REASSIGN_NAME_MAX]


async def _actor_name(user_id: str) -> str:
    """The reassigner's username (flattened, bounded), or a neutral fallback."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models.users import UserProfiles

    try:
        async with read_scope() as session:
            username = (
                await session.execute(
                    select(UserProfiles.username).where(
                        UserProfiles.id == UUID(user_id)
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — a missing name must not drop the note
        logger.warning(
            f"[issue_reassign] username lookup for {user_id} failed: {exc!r}"
        )
        return UNKNOWN_ACTOR
    return _flatten(username) if username else UNKNOWN_ACTOR


async def note_reassignment(
    *, existing: dict[str, Any], updated: dict[str, Any], actor_user_id: str
) -> bool:
    """Write the handoff note if the assignee agent changed. True if written.

    Nothing is written when the assignee is unchanged or cleared, or when the
    issue has no session yet (its first turn creates one already bound to the
    new agent — there is no prior transcript to explain). A failure is logged
    and swallowed: the reassignment is already committed and a decoration must
    not turn the PATCH into an error.
    """
    old, new = _assignee(existing), _assignee(updated)
    session_id = updated.get("ai_session_id") or existing.get("ai_session_id")
    if not new or new == old or not session_id:
        return False
    issue_id = updated.get("id") or existing.get("id")
    try:
        agent = await get_agent_repository().get_by_id(UUID(new))
        actor = await _actor_name(actor_user_id)
        await ConversationsAiStore().append_user_message(
            session_id=int(session_id),
            user_id=actor_user_id,
            content=f'Reassigned to "{_display_name(agent, new)}" by {actor}.',
            metadata={"issue_reassigned": {"from_agent_id": old, "to_agent_id": new}},
        )
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.opt(exception=True).error(
            f"[issue_reassign] issue={issue_id} session={session_id} {old}→{new}: "
            f"handoff note FAILED ({exc!r}); the reassignment itself stands"
        )
        return False
    logger.info(f"[issue_reassign] issue={issue_id} note written {old}→{new}")
    return True
