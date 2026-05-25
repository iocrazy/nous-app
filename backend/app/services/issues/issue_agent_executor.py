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


async def _load_user_providers(user_id: str) -> dict[str, Any]:
    """Load the user's BYO AI provider config (ai_settings.ai_providers) from
    user_settings. Returns {} when absent — get_adapter_for_user then falls
    back to platform keys per provider."""
    import json

    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT settings_json FROM public.user_settings WHERE user_id = :uid",
        {"uid": user_id},
    )
    if not row:
        return {}
    settings_json = row.get("settings_json")
    if isinstance(settings_json, str):
        try:
            settings_json = json.loads(settings_json)
        except (ValueError, TypeError):
            return {}
    ai_settings = (settings_json or {}).get("ai_settings", {}) or {}
    return ai_settings.get("ai_providers", {}) or {}


def _build_runner(
    composed: Any, settings: Any, user_providers: dict[str, Any]
) -> AgentRunner:
    """Adapter + AgentRunner for the composed agent. Uses the user's BYO
    provider config — get_adapter_for_user derives the provider from the model
    and falls back to platform keys per provider when the user hasn't set one.
    (Plain get_adapter/platform-only would fail for BYO-only deployments.)"""
    from app.services.ai.adapters.factory import get_adapter_for_user

    adapter = get_adapter_for_user(composed.model, user_providers, settings)
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

    user_providers = await _load_user_providers(user_id)
    runner = _build_runner(composed, settings, user_providers)
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
