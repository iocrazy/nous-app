"""Get-or-create the ai_session backing an issue's agent conversation (Spec-1a)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService


async def get_or_create_issue_session(issue_id: int) -> Optional[str]:
    """Return the issue's ai_session_id, creating + backfilling one if absent.

    Returns None if the issue has no assignable agent (nothing to run).
    """
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT ai_session_id, title, assignee_agent_id, created_by_user_id, "
        "assignee_user_id FROM public.issues WHERE id = :id",
        {"id": issue_id},
    )
    if not row:
        raise RuntimeError(f"issue {issue_id} not found")
    if row.get("ai_session_id"):
        return str(row["ai_session_id"])

    agent_id = row.get("assignee_agent_id")
    user_id = row.get("created_by_user_id") or row.get("assignee_user_id")
    if not agent_id or not user_id:
        return None

    agent = await get_agent_repository().get_by_id(UUID(str(agent_id)))
    if not agent:
        raise RuntimeError(f"assignee agent {agent_id} not found")

    # create_session expects user_id as UUID (keyword-only)
    session = await AILibraryChatService().create_session(
        user_id=UUID(str(user_id)),
        agent_slug=agent["slug"],
        title=(row.get("title") or "Issue")[:200],
        context_type="issue",
        context_id=str(issue_id),
    )
    session_id = str(session["id"])

    # Backfill, guarding on NULL so a concurrent create loses cleanly.
    # ai_session_id is BIGINT since mig 232 (snowflake ids) — asyncpg
    # requires a real int, a str raises DataError ('str' object cannot
    # be interpreted as an integer).
    n = await db_engine.execute(
        "UPDATE public.issues SET ai_session_id = :sid "
        "WHERE id = :id AND ai_session_id IS NULL",
        {"sid": int(session_id), "id": issue_id},
    )
    if n == 0:
        winner = await db_engine.fetch_one(
            "SELECT ai_session_id FROM public.issues WHERE id = :id", {"id": issue_id}
        )
        if winner and winner.get("ai_session_id"):
            return str(winner["ai_session_id"])
    logger.info(f"[issue_session] session {session_id} for issue {issue_id}")
    return session_id
