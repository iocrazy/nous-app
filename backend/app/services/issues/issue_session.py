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
    from sqlalchemy import select, update

    from app.db.session import read_scope, write_scope
    from app.models import Issues

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(
                        Issues.ai_session_id,
                        Issues.title,
                        Issues.assignee_agent_id,
                        Issues.created_by_user_id,
                        Issues.assignee_user_id,
                        Issues.project_id,
                        Issues.team_id,
                    ).where(Issues.id == issue_id)
                )
            )
            .mappings()
            .first()
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

    # M4 Autopilot fix (task O2 review C1): the issue's own project_id/team_id
    # MUST flow onto the session — RunRecorder tags agent_runs.project_id from
    # session["project_id"] (ai_library_chat_service._run_session_turn_inner),
    # and the autopilot daily quota counts agent_runs BY project_id
    # (agent_runs_repository.count_auto_dispatches_today). Without this, every
    # issue-dispatch session (including every project_stage mirror issue's)
    # created project_id=NULL, so agent_runs.project_id was NULL for every row
    # the quota counter filters on — the guardrail was structurally a no-op
    # (reviewer measured 80/80 rows NULL in prod). This also fixes project_id
    # attribution for every OTHER issue-dispatch consumer of agent_runs
    # (Usage panel project scoping, etc.), not just autopilot.
    chat_session = await AILibraryChatService().create_session(
        user_id=UUID(str(user_id)),
        agent_slug=agent["slug"],
        title=(row.get("title") or "Issue")[:200],
        project_id=row.get("project_id"),
        team_id=row.get("team_id"),
        context_type="issue",
        context_id=str(issue_id),
    )
    session_id = str(chat_session["id"])

    # Backfill, guarding on NULL so a concurrent create loses cleanly.
    # ai_session_id is BIGINT since mig 232 (snowflake ids) — asyncpg
    # requires a real int, a str raises DataError ('str' object cannot
    # be interpreted as an integer).
    async with write_scope() as session:
        result = await session.execute(
            update(Issues)
            .where(Issues.id == issue_id, Issues.ai_session_id.is_(None))
            .values(ai_session_id=int(session_id))
        )
        n = result.rowcount
    if n == 0:
        async with read_scope() as session:
            winner = (
                await session.execute(
                    select(Issues.ai_session_id).where(Issues.id == issue_id)
                )
            ).scalar_one_or_none()
        if winner:
            return str(winner)
    logger.info(f"[issue_session] session {session_id} for issue {issue_id}")
    return session_id
