"""Get-or-create the ai_session backing an issue's agent conversation (Spec-1a)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService


class IssueAssigneeNotFound(RuntimeError):
    """The issue's assignee agent could not be resolved when (re)binding its
    session. A RuntimeError subclass so every existing ``except RuntimeError``
    caller keeps working; typed so a caller can tell it apart."""

    def __init__(self, issue_id: int, agent_id: str) -> None:
        self.issue_id = issue_id
        self.agent_id = agent_id
        super().__init__(f"issue {issue_id}: assignee agent {agent_id} not found")


async def get_or_create_issue_session(
    issue_id: int, *, rebind: bool = True
) -> Optional[str]:
    """Return the issue's ai_session_id, creating + backfilling one if absent.

    Returns None if the issue has no assignable agent (nothing to run).

    When the session already exists it is re-bound to the CURRENT assignee
    (``_rebind_to_assignee``): reassigning an issue makes the next turn run the
    new agent on the same conversation — history is kept, like a human
    handoff, and no new session is created. A turn already in flight is not
    affected: it resolved its agent stack when it started, so the rebind takes
    effect from the next turn. Raises :class:`IssueAssigneeNotFound` when the
    new assignee cannot be resolved (the binding is left untouched).

    ``rebind=False`` is for callers that only need the id and never run a turn
    themselves (the subissue barrier's report, inbox delivery): an unresolvable
    assignee must not cost them the session. The turn choke points
    (``ensure_issue_session_step`` / ``run_issue_agent``) keep the default and
    rebind before the next turn anyway.

    Known consequences of rebinding the SAME conversation (human-handoff
    semantics, accepted):

    * a reassignment made while agent A's turn is in flight leaves A's reply
      after the handoff note in the history — A answers as if after it;
    * weekly memory consolidation (``consolidate_agent_memory``) selects
      conversations by ``conversation_ai_meta.agent_id``, so the whole
      pre-rebind history, A's replies included, counts toward the new agent.
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
        session_id = str(row["ai_session_id"])
        if rebind:
            await _rebind_to_assignee(
                issue_id, session_id, row.get("assignee_agent_id")
            )
        return session_id

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


async def _rebind_to_assignee(
    issue_id: int, session_id: str, assignee_agent_id: Any
) -> None:
    """Swing ``conversation_ai_meta.agent_id/agent_slug`` to the assignee.

    The chat runtime resolves the agent from the session's ``agent_slug``, so
    without this a reassigned issue kept running the agent it was created with
    while ``dispatch_preview`` and the UI named the new one. No-ops: assignee
    NULL (keep the current binding), meta row missing (``issues.ai_session_id``
    has no FK — nothing to swing), or already bound to the assignee (zero
    writes, no lookup).
    """
    if not assignee_agent_id:
        return
    from sqlalchemy import select, update

    from app.db.session import read_scope, write_scope
    from app.models.chat import ConversationAiMeta

    conversation_id = int(session_id)
    async with read_scope() as session:
        meta = (
            (
                await session.execute(
                    select(
                        ConversationAiMeta.agent_id, ConversationAiMeta.agent_slug
                    ).where(ConversationAiMeta.conversation_id == conversation_id)
                )
            )
            .mappings()
            .first()
        )
    if not meta:
        logger.warning(
            f"[issue_session] issue={issue_id} session={session_id} has no "
            "conversation_ai_meta row; not rebinding"
        )
        return
    new_id = str(assignee_agent_id)
    old_id = meta.get("agent_id")
    if old_id is not None and str(old_id) == new_id:
        return

    agent = await get_agent_repository().get_by_id(UUID(new_id))
    if not agent or not agent.get("slug"):
        raise IssueAssigneeNotFound(issue_id, new_id)
    async with write_scope() as session:
        await session.execute(
            update(ConversationAiMeta)
            .where(ConversationAiMeta.conversation_id == conversation_id)
            .values(agent_id=UUID(new_id), agent_slug=agent["slug"])
        )
    logger.info(
        f"[issue_session] rebind issue={issue_id} session={session_id} "
        f"{meta.get('agent_slug')}({old_id})→{agent['slug']}({new_id})"
    )
