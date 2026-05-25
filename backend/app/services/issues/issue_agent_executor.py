"""Run the agent assigned to an issue and record its output.

The result write-back to the issue chat is handled by the mig-208 DB triggers:
the issue-linked agent_runs row (RunRecorder with issue_id set) emits an
"Agent picking up..." issue_messages placeholder on INSERT, and the terminal
trigger copies agent_runs.output_summary into that row's body when the run
completes. So this module only runs the agent and sets output_summary.

Mirrors app/services/ai/summarize/summarize_service.py.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService


def _build_runner(composed: Any, settings: Any) -> AgentRunner:
    """Build an adapter + AgentRunner for the composed agent (platform keys)."""
    from app.services.ai.adapters.factory import get_adapter

    adapter = get_adapter(composed.model, settings)
    return AgentRunner(adapter=adapter, skill_tool=SkillToolService(SkillRepository()))


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
    """Compose + run the assigned agent on the issue.

    Returns the agent's text output (also persisted via
    RunRecorder.output_summary → mig-208 bridge).

    Raises RuntimeError when the agent row is not found in ai_agents.
    """
    from app.core.config import settings

    agent_repo = AgentRepository()
    agent_row = await agent_repo.get_by_id(UUID(agent_id))
    if not agent_row:
        raise RuntimeError(f"assignee agent {agent_id} not found")

    composer = PromptComposer(agent_repo, SkillRepository())
    composed = await composer.compose(
        ComposerInput(
            agent_slug=agent_row["slug"],
            request_instructions=_build_user_message(issue),
        )
    )

    runner = _build_runner(composed, settings)
    user_messages = [{"role": "user", "content": _build_user_message(issue)}]

    async with RunRecorder(
        agent_id=composed.agent_id,
        user_id=UUID(user_id),
        trigger="issue_dispatch",
        model=composed.model,
        issue_id=int(issue["id"]),
    ) as recorder:
        result = await runner.run_turn(composed, user_messages, recorder=recorder)
        content = result.get("content") or ""
        recorder.set_summaries(
            input_summary=_build_user_message(issue)[:500],
            output_summary=content[:500] if content else "(no output)",
        )

    logger.info(
        f"[issue_agent] issue={issue['id']} agent={agent_id} produced "
        f"{len(content)} chars"
    )
    return content
