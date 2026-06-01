"""Script AI Service — DB-driven outline, expansion, and branching.

All prompts are sourced from the ``ai_agents`` row with slug ``script_ai``
and its bound skills (see migration 138). This service is intentionally
thin: it composes the system message via :class:`PromptComposer`, then
delegates LLM execution (and tool-call resolution) to :class:`AgentRunner`.

Public methods preserve their original signatures so existing callers
(``app.api.script_ai_router``, ``app.tasks.script_tasks``) keep working
without changes:

* ``generate_outline``       → JSON array of chapters
* ``expand_chapter``         → sanitized HTML string
* ``create_branches``        → JSON array of branch objects
* ``split_chapter_to_scenes``→ JSON array of scene objects

Each method builds a small per-request instruction string (the only
place task-specific guidance lives outside the DB) and runs one agent
turn. Post-processing (JSON extraction, HTML sanitization, field
coercion) matches the behaviour of the previous hardcoded version.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

import bleach
from loguru import logger

from app.core.config import settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.ai.adapters import get_adapter
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService

ALLOWED_HTML_TAGS = ["h2", "h3", "p", "strong", "em", "hr", "br"]


def sanitize_ai_html(html: str) -> str:
    """Sanitize AI-generated HTML, only allow script-safe tags."""
    return bleach.clean(html, tags=ALLOWED_HTML_TAGS, strip=True)


# Output safety limits
MAX_TITLE_LENGTH = 200
MAX_SUMMARY_LENGTH = 5000
MAX_CONTENT_LENGTH = 50000
MAX_BRANCH_LABEL_LENGTH = 100

# Agent slug in ai_agents table (seeded by migration 138 + seed_loader)
AGENT_SLUG = "script_ai"


class ScriptAIService:
    """AI operations for the script editor module (DB-driven prompts)."""

    AGENT_SLUG: str = AGENT_SLUG

    def __init__(self, user_id: Optional[Any] = None) -> None:
        # Retained for backwards compat with legacy smoke tests that
        # inspect ``.model``. The actual model per turn comes from the
        # agent row via :class:`PromptComposer`.
        self.model = settings.LLM_MODEL
        # Optional user_id enables RunRecorder telemetry on each _run_agent
        # call. When None, telemetry is skipped (legacy / smoke-test path).
        self._user_id = user_id

    # ------------------------------------------------------------------
    # Shared plumbing — composer / runner wiring
    # ------------------------------------------------------------------

    def _build_composer(self) -> PromptComposer:
        return PromptComposer(AgentRepository(), SkillRepository())

    def _build_runner(self, model: str = "") -> AgentRunner:
        # Pick adapter based on the agent's configured model. Empty / unknown-to-
        # the-factory models fall back to QwenAdapter + settings.LLM_MODEL for
        # Phase 1 compat. The model is threaded in from ComposedSystemPrompt
        # (composer.compose() pulls it from the ai_agents row), not read from
        # global settings, so agents can declare their own provider in DB.
        adapter = get_adapter(model, settings)
        return AgentRunner(
            adapter=adapter, skill_tool=SkillToolService(SkillRepository())
        )

    async def _run_agent(
        self,
        request_instructions: str,
        user_content: str,
        *,
        user_id: Optional[Any] = None,
        # ai_sessions.id is BIGINT Snowflake (mig 231) → numeric string.
        session_id: Optional[str] = None,
        team_id: Optional[int] = None,
        project_id: Optional[int] = None,
    ) -> str:
        """Compose the agent prompt and run one turn. Returns raw LLM content.

        When ``user_id`` is provided, wraps the run in :class:`RunRecorder`
        so an ``agent_runs`` row is persisted with tokens / cost / outcome.
        Callers without a user_id (rare — e.g. internal smoke tests) still
        work but skip telemetry.
        """
        composer = self._build_composer()
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=request_instructions,
            )
        )
        runner = self._build_runner(composed.model or "")
        user_messages = [{"role": "user", "content": user_content}]

        # Resolve user_id: explicit arg wins, else fall back to instance's
        effective_user = user_id if user_id is not None else self._user_id

        if effective_user is None:
            # No-telemetry path (same behaviour as pre-C0).
            result = await runner.run_turn(composed, user_messages=user_messages)
        else:
            uid = (
                effective_user
                if isinstance(effective_user, UUID)
                else UUID(str(effective_user))
            )
            model = composed.model or ""
            try:
                provider = provider_key_for_model(model) if model else None
            except ValueError:
                provider = None
            try:
                async with RunRecorder(
                    agent_id=composed.agent_id,
                    user_id=uid,
                    trigger="script_ai",
                    session_id=session_id,
                    team_id=team_id,
                    project_id=project_id,
                    model=model or None,
                    provider=provider,
                    input_summary=user_content,
                    metadata={"full_input": user_content},
                ) as recorder:
                    result = await runner.run_turn(
                        composed,
                        user_messages=user_messages,
                        recorder=recorder,
                    )
                    recorder.set_summaries(output_summary=result.get("content") or "")
            except AgentPausedError as err:
                logger.warning(f"[ScriptAI] agent paused: {err}")
                raise

        if result.get("error"):
            logger.warning(
                "[ScriptAI] agent runner returned error: %s", result.get("error")
            )
        return result.get("content", "") or ""

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------

    def _extract_json(self, text: str) -> Any:
        """Extract JSON from LLM response that may be wrapped in markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1
            if lines[-1].strip() == "```":
                cleaned = "\n".join(lines[start:end])
            else:
                cleaned = "\n".join(lines[start:])
        return json.loads(cleaned)

    # ------------------------------------------------------------------
    # Public API — signatures preserved from the hardcoded version
    # ------------------------------------------------------------------

    async def generate_outline(
        self,
        premise: str,
        chapter_count: int = 5,
        style_guide: Optional[str] = None,
        genre: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Generate a story outline with chapter summaries from a premise."""
        request_instructions = (
            "Task: generate story outline.\n"
            "Return a JSON array of chapter objects. Each object must have:\n"
            '- "title": string (chapter title)\n'
            '- "summary": string (2-3 sentence plot summary)\n'
            f"Generate exactly {chapter_count} chapters.\n"
            "Return ONLY a JSON array, no other text."
        )

        user_prompt = f"Story premise:\n{premise}"
        if genre:
            user_prompt += f"\n\n故事风格为{genre}，请围绕该风格创作。"
        if style_guide:
            user_prompt += f"\n\nStyle guide:\n{style_guide}"

        response = await self._run_agent(request_instructions, user_prompt)
        chapters = self._extract_json(response)

        if not isinstance(chapters, list):
            raise ValueError("LLM did not return a JSON array")

        sanitized: List[Dict[str, str]] = []
        for i, ch in enumerate(chapters):
            if not isinstance(ch, dict):
                continue
            title = ch.get("title", f"Chapter {i + 1}")
            if not isinstance(title, str):
                title = str(title)[:MAX_TITLE_LENGTH]
            else:
                title = title[:MAX_TITLE_LENGTH]
            summary = ch.get("summary", "")
            if not isinstance(summary, str):
                summary = str(summary)[:MAX_SUMMARY_LENGTH]
            else:
                summary = summary[:MAX_SUMMARY_LENGTH]
            sanitized.append({"title": title, "summary": summary})
        return sanitized

    async def expand_chapter(
        self,
        title: str,
        summary: str,
        context: Optional[str] = None,
        expansion_request: Optional[str] = None,
    ) -> str:
        """Expand a chapter summary into full screenplay HTML content."""
        request_instructions = (
            "Task: expand chapter into screenplay HTML.\n"
            "OUTPUT FORMAT (mandatory):\n"
            "- Scene headings: <h2>场景N：场景名 – 时间 – 内/外景</h2>\n"
            "- Action/description: <p>paragraph text</p>\n"
            "- Character dialogue: <p><strong>角色名</strong>：（动作描述）台词内容</p>\n"
            "- Scene separator: <hr>\n"
            "- Do NOT wrap output in any container tags. Output raw HTML fragments only.\n"
            "- Do NOT output markdown. Only the HTML tags listed above."
        )

        user_prompt = f"Chapter title: {title}\nSummary: {summary}"
        if context:
            user_prompt = f"Story context:\n{context}\n\n{user_prompt}"
        if expansion_request:
            user_prompt += f"\n\nAdditional requirements: {expansion_request}"

        content = await self._run_agent(request_instructions, user_prompt)
        return sanitize_ai_html(content[:MAX_CONTENT_LENGTH])

    async def create_branches(
        self,
        title: str,
        summary: str,
        branch_count: int = 2,
        branch_type: str = "choice",
        context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Generate alternative story branches from a chapter."""
        request_instructions = (
            "Task: create branching story alternatives.\n"
            f"Create exactly {branch_count} alternative story branches "
            f"from the given chapter. Branch type: {branch_type}.\n"
            "For 'choice' type: each branch represents a different decision "
            "the protagonist could make.\n"
            "For 'condition' type: each branch represents a different "
            "circumstance that could unfold.\n"
            "Return a JSON array of branch objects, each with:\n"
            '- "title": string (branch chapter title)\n'
            '- "summary": string (2-3 sentence plot summary for this branch)\n'
            '- "branch_label": string (short label like "Fight" or "Flee")\n'
            "Return ONLY a JSON array, no other text."
        )

        user_prompt = f"Chapter: {title}\nSummary: {summary}"
        if context:
            user_prompt = f"Story context:\n{context}\n\n{user_prompt}"

        response = await self._run_agent(request_instructions, user_prompt)
        branches = self._extract_json(response)

        if not isinstance(branches, list):
            raise ValueError("LLM did not return a JSON array")

        sanitized: List[Dict[str, str]] = []
        for i, b in enumerate(branches[:branch_count]):
            if not isinstance(b, dict):
                continue
            b_title = b.get("title", f"Branch {i + 1}")
            if not isinstance(b_title, str):
                b_title = str(b_title)[:MAX_TITLE_LENGTH]
            else:
                b_title = b_title[:MAX_TITLE_LENGTH]
            b_summary = b.get("summary", "")
            if not isinstance(b_summary, str):
                b_summary = str(b_summary)[:MAX_SUMMARY_LENGTH]
            else:
                b_summary = b_summary[:MAX_SUMMARY_LENGTH]
            branch_label = b.get("branch_label", f"Path {i + 1}")
            if not isinstance(branch_label, str):
                branch_label = str(branch_label)[:MAX_BRANCH_LABEL_LENGTH]
            else:
                branch_label = branch_label[:MAX_BRANCH_LABEL_LENGTH]
            sanitized.append(
                {
                    "title": b_title,
                    "summary": b_summary,
                    "branch_label": branch_label,
                }
            )
        return sanitized

    async def split_chapter_to_scenes(
        self,
        title: str,
        summary: str,
        content: Optional[str] = None,
        style_guide: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Split a chapter into 3-8 visual scenes for storyboard conversion."""
        request_instructions = (
            "Task: split chapter into visual scenes for storyboard.\n"
            "Produce 3-8 distinct visual scenes.\n"
            "Each scene object must have:\n"
            '- "scene_number": int (sequential starting from 1)\n'
            '- "description": string (detailed visual description — what is '
            "happening, who is present, setting details)\n"
            '- "camera_notes": string (camera angle, shot type, mood, '
            "lighting suggestions)\n"
            "Return ONLY a JSON array, no other text."
        )

        user_prompt = f"Chapter title: {title}\nSummary: {summary}"
        if content:
            user_prompt += f"\n\nFull content:\n{content}"
        if style_guide:
            user_prompt += f"\n\nStyle guide:\n{style_guide}"

        response = await self._run_agent(request_instructions, user_prompt)
        scenes = self._extract_json(response)

        if not isinstance(scenes, list):
            raise ValueError("LLM did not return a JSON array")

        return [
            {
                "scene_number": s.get("scene_number", i + 1),
                "description": s.get("description", ""),
                "camera_notes": s.get("camera_notes", ""),
            }
            for i, s in enumerate(scenes[:8])
        ]
