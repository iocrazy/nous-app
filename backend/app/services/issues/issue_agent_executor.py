"""Run the agent assigned to an issue on the FULL chat runtime (Spec-1a).

The issue is backed by an ai_session (get_or_create_issue_session); execution
reuses AILibraryChatService.run_session_turn — the same turn flow chat() runs —
so memory, compaction, sub-agents, delegation, budget, fallback, and BYO-key
adapter resolution all apply. The agent's reply is persisted as an ai_message
by the turn flow; the issue chat surface reads ai_messages (Task 5).
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
from app.services.issues.issue_session import get_or_create_issue_session


def _build_user_message(issue: dict[str, Any]) -> str:
    """Compose a user message from an issue's title and optional description."""
    title = (issue.get("title") or "").strip()
    description = (issue.get("description") or "").strip()
    parts = [f"Task: {title}"] if title else []
    if description:
        parts.append(f"\nDetails:\n{description}")
    return "\n".join(parts) or "Complete the assigned task."


async def run_issue_agent(
    *, issue: dict[str, Any], agent_id: str, user_id: str
) -> Optional[str]:
    """Run the assigned agent on the issue via the chat runtime.

    Returns the agent's text output (also persisted as an ai_message by
    run_session_turn).

    Raises RuntimeError when the issue has no assignable agent session.

    ``agent_id`` is accepted for caller-signature compatibility
    (run_issue_agent_step passes it) but is unused here — the session already
    binds the agent.
    """
    _ = agent_id  # session already binds the agent; kept for caller compat

    session_id = await get_or_create_issue_session(int(issue["id"]))
    if not session_id:
        raise RuntimeError(f"issue {issue['id']} has no assignable agent session")

    result = await AILibraryChatService().run_session_turn(
        session_id,
        user_id=user_id,
        content=_build_user_message(issue),
        trigger="issue_dispatch",
    )
    content = (result.get("assistant_message") or {}).get("content") or ""
    logger.info(
        f"[issue_agent] issue={issue['id']} session={session_id} "
        f"produced {len(content)} chars"
    )
    return content
