"""Run the agent assigned to an issue on the FULL chat runtime (Spec-1a).

The issue is backed by an ai_session (get_or_create_issue_session); execution
reuses AILibraryChatService.run_session_turn — the same turn flow chat() runs —
so memory, compaction, sub-agents, delegation, budget, fallback, and BYO-key
adapter resolution all apply. The agent's reply is persisted as an ai_message
by the turn flow; the issue chat surface reads ai_messages (Task 5).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
from app.services.ai.tools.finish_issue_tool import extract_issue_outcome
from app.services.issues.issue_chat_stream import (  # noqa: F401
    publish_chunk,
    publish_message,
    publish_status,
)
from app.services.issues.issue_session import get_or_create_issue_session


def _build_user_message(issue: dict[str, Any]) -> str:
    """Compose a user message from an issue's title and optional description."""
    title = (issue.get("title") or "").strip()
    description = (issue.get("description") or "").strip()
    parts = [f"Task: {title}"] if title else []
    if description:
        parts.append(f"\nDetails:\n{description}")
    return "\n".join(parts) or "Complete the assigned task."


# Synthetic nudge for a continuation turn (Spec-2). The session already carries
# the full task history via memory, so we only need to prompt another turn.
CONTINUATION_NUDGE = (
    "Continue working on this issue. When you are finished, blocked, or need "
    "another turn, call the FinishIssue tool to declare the outcome."
)


async def run_issue_agent(
    *,
    issue: dict[str, Any],
    agent_id: str,
    user_id: str,
    is_continuation: bool = False,
) -> dict[str, Any]:
    """Run the assigned agent on the issue via the chat runtime.

    Streams token deltas + publishes the final message to Redis channel
    ``issue:{id}`` while the turn is in flight.

    Returns ``{"content": str, "outcome": Optional[str], "reason": Optional[str]}``
    where ``outcome`` is the agent's FinishIssue declaration (completed |
    needs_input | continue) or None if it never declared. The assistant text is
    also persisted as an ai_message by run_session_turn.

    ``is_continuation`` sends a short "keep going" nudge instead of the full
    task text (Spec-2 bounded continuation); the agent already has the history.

    Raises RuntimeError when the issue has no assignable agent session.

    ``agent_id`` is accepted for caller-signature compatibility
    (run_issue_agent_step passes it) but is unused here — the session already
    binds the agent.
    """
    _ = agent_id  # session already binds the agent; kept for caller compat

    iid = int(issue["id"])
    session_id = await get_or_create_issue_session(iid)
    if not session_id:
        raise RuntimeError(f"issue {issue['id']} has no assignable agent session")

    async def _cb(delta: str) -> None:
        await publish_chunk(iid, delta)

    content_in = CONTINUATION_NUDGE if is_continuation else _build_user_message(issue)

    await publish_status(iid, "running")
    try:
        result = await AILibraryChatService().run_session_turn(
            session_id,
            user_id=user_id,
            content=content_in,
            trigger="issue_dispatch",
            chunk_callback=_cb,
        )
        assistant = result.get("assistant_message") or {}
        await publish_message(iid, assistant, session_user_id=None)
        content = assistant.get("content") or ""
        outcome, reason = extract_issue_outcome(result.get("tool_calls"))
        logger.info(
            f"[issue_agent] issue={iid} session={session_id} "
            f"produced {len(content)} chars; outcome={outcome}"
        )
        return {"content": content, "outcome": outcome, "reason": reason}
    finally:
        await publish_status(iid, "done")
