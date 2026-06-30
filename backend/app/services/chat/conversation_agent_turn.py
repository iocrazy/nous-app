"""Stub for Task 6: conversation agent turn runner.

Task 6 will replace this body with the real implementation.
ConversationService imports this module at the top level so tests
can monkeypatch it cleanly.
"""

from __future__ import annotations

from typing import Any, Optional


async def run_conversation_agent_turn(
    *,
    agent_slug: str,
    summoner_user_id: str,
    conversation: dict[str, Any],
) -> Optional[str]:
    """Run one LLM turn for *agent_slug* inside *conversation*.

    Returns the agent's reply text, or None when the agent declines.
    Task 6 will replace this stub body.
    """
    raise NotImplementedError("Task 6 not yet implemented")
