# backend/app/services/llm_analysis_service.py

"""LLM analysis service — transcript summarization.

Phase 2 PR 2.4 migrated this from a hardcoded-prompt design to the AI
Library agent framework. The prompt (IDENTITY / SOUL / AGENT) now lives
in the `summarize` ``ai_agents`` row, composed via :class:`PromptComposer`
and executed via :class:`AgentRunner`.

Per-user provider routing (picked by the Celery task from the user's
``task_assignment.summarization`` setting) is preserved by letting the
caller inject an explicit adapter — bypassing :func:`get_adapter`'s
DB-model dispatch since summarize's model is chosen at call time, not
in the agent row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.config import settings as app_settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.ai_repository import AIRepository
from app.repositories.skill_repository import SkillRepository
from app.services.agent_runner import AgentRunner
from app.services.ai_adapters.base import AIAdapter
from app.services.ai_adapters.factory import get_adapter_for_user
from app.services.ai_adapters.openai_compat import OpenAICompatibleAdapter
from app.services.prompt_composer import ComposerInput, PromptComposer
from app.services.skill_tool_service import SkillToolService

# Agent slug in the ai_agents table (seeded from backend/seeds/agents/summarize/).
AGENT_SLUG = "summarize"


@dataclass
class SummaryResult:
    summary: str
    key_points: List[str]
    topics: List[str]


# Keys must match frontend/components/AISettings.tsx LANGUAGE_OPTIONS.
# "auto" deliberately yields no directive so the model follows the transcript.
_LANGUAGE_DIRECTIVES: Dict[str, str] = {
    "auto": "",
    "en": "Respond in English.",
    "zh": "请用简体中文回复（summary、key_points、topics 全部用中文）。",
    "ja": "Respond in Japanese.",
    "ko": "Respond in Korean.",
    "es": "Respond in Spanish.",
    "fr": "Respond in French.",
    "de": "Respond in German.",
}


def _build_language_directive(language: str) -> str:
    if not language:
        return ""
    return _LANGUAGE_DIRECTIVES.get(language.lower(), "")


def _build_adapter_from_provider_config(
    provider_key: str, provider_config: Dict[str, Any]
) -> AIAdapter:
    """Build an adapter from the Celery task's per-user provider config.

    As of Phase 2 PR 2.8b (migration 142), ``task_assignment.summarization``
    is an agent slug. The Celery task resolves that slug to an agent row,
    derives the provider from the agent's ``model`` field, and hands us a
    ``provider_config`` reflecting the user's BYO credentials (with a global
    settings fallback already applied at the task layer).

    For native multi-provider support we route through
    :func:`get_adapter_for_user`, which picks the right adapter class
    (Qwen / Doubao / DeepSeek / Claude) per model prefix. Unknown providers
    fall through to a generic OpenAI-compatible adapter so callers that
    hand-craft a custom OpenAI endpoint still work.
    """
    model = provider_config.get("model", "") or ""
    user_cfg_scoped = {
        provider_key: {
            "api_key": provider_config.get("api_key", ""),
            "base_url": provider_config.get("base_url", "") or "",
            "app_id": provider_config.get("app_id", ""),
        }
    }
    try:
        return get_adapter_for_user(model, user_cfg_scoped, app_settings)
    except ValueError:
        # Unknown model prefix (e.g. an OpenAI-compatible custom endpoint
        # with a non-standard model name). Fall back to the generic adapter
        # using whatever api_url / api_key the task handed us.
        return OpenAICompatibleAdapter(
            api_url=provider_config.get("base_url", "") or "",
            api_key=provider_config.get("api_key", ""),
            default_model=model,
        )


class LLMAnalysisService:
    """LLM-powered analysis for video transcripts and content.

    Prompts come from the ``summarize`` ai_agents row. The caller supplies
    the provider/model (preserving user-scoped task-assignment + Nous
    platform routing from the Celery task layer).
    """

    AGENT_SLUG: str = AGENT_SLUG

    def __init__(
        self,
        provider_key: str = "openai",
        provider_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._provider_key = provider_key
        self._provider_config = provider_config or {}
        self._repo = AIRepository()

    # ------------------------------------------------------------------
    # Shared plumbing — composer / runner wiring
    # ------------------------------------------------------------------

    def _build_composer(self) -> PromptComposer:
        return PromptComposer(AgentRepository(), SkillRepository())

    def _build_runner(self) -> AgentRunner:
        """Build a runner whose adapter is injected from the caller's
        provider_config (per-user BYO key), not from global settings."""
        adapter = _build_adapter_from_provider_config(
            self._provider_key, self._provider_config
        )
        return AgentRunner(
            adapter=adapter, skill_tool=SkillToolService(SkillRepository())
        )

    async def _run_agent(
        self, request_instructions: str, user_content: str, model: Optional[str]
    ) -> str:
        composer = self._build_composer()
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=request_instructions,
            )
        )
        # Caller's per-request model takes precedence over the agent row's
        # model — summarize's model routing is set per-user in the task
        # assignment UI, not in the agent row.
        if model:
            composed = composed.model_copy(update={"model": model})
        runner = self._build_runner()
        result = await runner.run_turn(
            composed,
            user_messages=[{"role": "user", "content": user_content}],
        )
        if result.get("error"):
            logger.warning(
                "[Summarize] agent runner returned error: %s", result.get("error")
            )
        return result.get("content", "") or ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_summary(
        self,
        transcript_text: str,
        video_info: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        language: str = "auto",
    ) -> SummaryResult:
        """Generate a summary from a transcript."""
        context_parts: List[str] = []
        if video_info:
            if video_info.get("title"):
                context_parts.append(f"Title: {video_info['title']}")
            if video_info.get("description"):
                context_parts.append(f"Description: {video_info['description']}")
            if video_info.get("author"):
                context_parts.append(f"Author: {video_info['author']}")
        context_str = "\n".join(context_parts) if context_parts else ""

        language_directive = _build_language_directive(language)

        # `request_instructions` carries per-call dynamic guidance. The
        # static task definition + output schema + style rules live in
        # the agent's AGENT.md (composed into the system prompt).
        instruction_bits = ["Task: summarize the transcript below."]
        if language_directive:
            instruction_bits.append(language_directive)
        if context_str:
            instruction_bits.append(f"Video metadata:\n{context_str}")
        request_instructions = "\n\n".join(instruction_bits)

        # Truncate transcript to the budget the original hardcoded prompt used.
        user_content = f"Transcript:\n{transcript_text[:8000]}"

        logger.info(
            f"[Summarize] provider={self._provider_key} model={model or '(default)'} "
            f"language={language}"
        )
        response_text = await self._run_agent(request_instructions, user_content, model)
        return self._parse_summary_response(response_text)

    async def generate_summary_and_save(
        self,
        resource_id: str,
        transcript_text: str,
        video_info: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        language: str = "auto",
    ) -> Optional[SummaryResult]:
        """Generate summary and persist to database."""
        try:
            result = await self.generate_summary(
                transcript_text, video_info, model, language=language
            )
            await self._repo.save_summary(
                resource_id,
                {
                    "summary_type": "transcript",
                    "summary_text": result.summary,
                    "key_points": result.key_points,
                    "topics": result.topics,
                    "llm_model": model or self._provider_config.get("model", ""),
                    "llm_provider": self._provider_key,
                },
            )
            logger.info(f"Summary saved for resource {resource_id}")
            return result
        except Exception as e:
            logger.error(f"Summary generation failed for resource {resource_id}: {e}")
            raise

    def _parse_summary_response(self, text: str) -> SummaryResult:
        """Parse LLM JSON response into SummaryResult. Tolerant of markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        try:
            data = json.loads(cleaned)
            return SummaryResult(
                summary=data.get("summary", ""),
                key_points=data.get("key_points", []),
                topics=data.get("topics", []),
            )
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse LLM response as JSON, using raw text as summary"
            )
            return SummaryResult(
                summary=text.strip()[:500],
                key_points=[],
                topics=[],
            )
