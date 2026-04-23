"""Visual analysis service using GPT-4o with multimodal prompts.

V1 (runner-multimodal): the ``analyze`` agent now runs through
``AgentRunner`` + ``RunRecorder`` like every other AI service. The
OpenAI-native closure that used to bypass the runner is gone; the
factory-registered :class:`OpenAIAdapter` carries multimodal content
arrays straight through to GPT-4o's chat-completions endpoint.

This matches the pattern used by ``script_ai_service`` /
``llm_analysis_service`` / ``storyboard_ai_service``: compose prompt
from DB, build runner via adapter factory, wrap in RunRecorder, run.

Per-request ``L1`` vs ``L2`` selection is carried through the composer's
``request_instructions`` field. The agent's ``AGENT.md`` documents both
modes; the instruction tells the model which mode to operate in for this
call.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from loguru import logger

from app.core.config import settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.agent_runner import AgentRunner
from app.services.ai_adapters.factory import get_adapter, provider_key_for_model
from app.services.prompt_composer import ComposerInput, PromptComposer
from app.services.run_recorder import AgentPausedError, RunRecorder
from app.services.skill_tool_service import SkillToolService

# Agent slug in the ai_agents table (seeded from backend/seeds/agents/analyze/).
AGENT_SLUG = "analyze"

# Per-request instructions differentiating the two analysis modes.
_L1_INSTRUCTION = (
    "Mode: L1 (cover image only). Follow the L1 section of your AGENT spec. "
    "Return the L1 JSON shape (no content_summary field). "
    "Return ONLY JSON with no markdown fences."
)
_L2_INSTRUCTION = (
    "Mode: L2 (cover + 3 keyframes at 25% / 50% / 75%). Follow the L2 section "
    "of your AGENT spec. Return the L2 JSON shape including content_summary. "
    "Return ONLY JSON with no markdown fences."
)


@dataclass
class VisualAnalysisResult:
    """Result of visual analysis."""

    category: str
    visual_description: str
    detected_objects: List[str] = field(default_factory=list)
    detected_scenes: List[str] = field(default_factory=list)
    detected_people: List[Dict[str, str]] = field(default_factory=list)
    detected_text: str = ""
    mood: str = ""
    cost: float = 0.0


class VisualAnalysisService:
    """Service for analyzing video content using AI (DB prompt + AgentRunner)."""

    AGENT_SLUG: str = AGENT_SLUG

    def __init__(self) -> None:
        # GPT-4o pricing for cost estimation — kept here because the
        # ``agent_runs`` row already carries authoritative cost via the
        # price-snapshot columns. This estimate is just for the
        # dataclass return field, which downstream code treats as
        # informational.
        self.model = settings.OPENAI_MODEL or "gpt-4o"

    # ── Image encoding helpers ────────────────────────────────────────

    async def _encode_image_from_url(self, url: str) -> Optional[str]:
        """Download and encode image to base64."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                if response.status_code == 200:
                    return base64.b64encode(response.content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
        return None

    async def _encode_image_from_file(self, file_path: str) -> Optional[str]:
        """Read and encode local image to base64."""
        try:
            import aiofiles

            async with aiofiles.open(file_path, "rb") as f:
                data = await f.read()
            return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to read image from {file_path}: {e}")
        return None

    # ── Result assembly ───────────────────────────────────────────────

    @staticmethod
    def _result_from_json(data: Dict[str, Any], cost: float) -> VisualAnalysisResult:
        """Build a VisualAnalysisResult from the parsed JSON payload."""
        return VisualAnalysisResult(
            category=data.get("category", "Other"),
            visual_description=data.get("visual_description", ""),
            detected_objects=data.get("detected_objects", []),
            detected_scenes=data.get("detected_scenes", []),
            detected_people=data.get("detected_people", []),
            detected_text=data.get("detected_text", ""),
            mood=data.get("mood", ""),
            cost=cost,
        )

    @staticmethod
    def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
        """GPT-4o pricing (approx): $2.50 / 1M input, $10 / 1M output."""
        return (prompt_tokens * 0.0025 + completion_tokens * 0.01) / 1000

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """Parse JSON from the agent's content string.

        The AgentRunner returns the raw text; some models wrap it in
        ```json fences even when asked not to, so strip them.
        """
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[-1].strip() == "```":
                cleaned = "\n".join(lines[1:-1])
            else:
                cleaned = "\n".join(lines[1:])
        if not cleaned:
            return {}
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as err:
            logger.warning(f"[VisualAnalysis] JSON parse failed: {err}")
            return {}

    # ── Shared runner invocation ──────────────────────────────────────

    async def _run_multimodal(
        self,
        *,
        instruction: str,
        image_content_blocks: List[Dict[str, Any]],
        input_summary: str,
        metadata: Dict[str, Any],
        user_id: Optional[Any],
        trigger: str,
    ) -> Optional[VisualAnalysisResult]:
        """Compose prompt + run one turn with multimodal content.

        When ``user_id`` is None (legacy / smoke test path), skips the
        RunRecorder wrap — same shape as script_ai_service. Otherwise
        wraps so agent_runs gets its row.
        """
        # Compose system prompt from the analyze agent's IDENTITY/SOUL/AGENT.
        composer = PromptComposer(AgentRepository(), SkillRepository())
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=instruction,
            )
        )

        adapter = get_adapter(composed.model or self.model, settings)
        runner = AgentRunner(
            adapter=adapter,
            skill_tool=SkillToolService(SkillRepository()),
        )

        # The user turn is a single message whose content is the array
        # of image blocks. OpenAI's chat-completions API accepts this
        # shape directly; the OpenAICompatibleAdapter passes it through
        # without re-serialization.
        user_messages = [{"role": "user", "content": image_content_blocks}]

        async def _run() -> Optional[VisualAnalysisResult]:
            result = await runner.run_turn(
                composed, user_messages=user_messages, recorder=None
            )
            if result.get("error"):
                logger.warning(f"[VisualAnalysis] runner error: {result.get('error')}")
                return None
            data = self._extract_json(result.get("content") or "")
            # Usage isn't captured in the bare path; cost stays 0 for the
            # dataclass return field and authoritative cost is on the
            # agent_runs row (when RunRecorder is wrapped).
            return self._result_from_json(data, 0.0)

        if user_id is None:
            try:
                return await _run()
            except Exception as e:
                logger.error(f"[VisualAnalysis] bare run failed: {e}")
                return None

        uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        model = composed.model or self.model
        try:
            provider = provider_key_for_model(model) if model else "openai"
        except ValueError:
            provider = "openai"

        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=uid,
                trigger=trigger,
                model=model or None,
                provider=provider,
                input_summary=input_summary,
                metadata=metadata,
            ) as recorder:
                result = await runner.run_turn(
                    composed,
                    user_messages=user_messages,
                    recorder=recorder,
                )
                content = result.get("content") or ""
                recorder.set_summaries(output_summary=content)
                if result.get("error"):
                    logger.warning(
                        f"[VisualAnalysis] runner error: {result.get('error')}"
                    )
                    return None
                data = self._extract_json(content)
                cost = self._estimate_cost(
                    recorder.prompt_tokens, recorder.completion_tokens
                )
                return self._result_from_json(data, cost)
        except AgentPausedError as err:
            logger.warning(f"[VisualAnalysis] agent paused: {err}")
            return None
        except Exception as e:
            logger.error(f"[VisualAnalysis] {trigger} failed: {e}")
            return None

    # ── Public API ────────────────────────────────────────────────────

    async def analyze_l1(
        self, cover_url: str, *, user_id: Optional[Any] = None
    ) -> Optional[VisualAnalysisResult]:
        """L1 Analysis: cover image only. Cost: ~$0.001 per image."""
        image_data = await self._encode_image_from_url(cover_url)
        if not image_data:
            logger.error(f"Failed to encode cover image: {cover_url}")
            return None

        image_blocks = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_data}",
                    "detail": "low",
                },
            },
        ]
        return await self._run_multimodal(
            instruction=_L1_INSTRUCTION,
            image_content_blocks=image_blocks,
            input_summary=f"L1 analysis of {cover_url}",
            metadata={"mode": "L1", "cover_url": cover_url},
            user_id=user_id,
            trigger="visual_analysis_l1",
        )

    async def analyze_l2(
        self,
        cover_url: str,
        keyframe_paths: List[str],
        *,
        user_id: Optional[Any] = None,
    ) -> Optional[VisualAnalysisResult]:
        """L2 Analysis: cover + keyframes. Cost: ~$0.005 per video."""
        images: List[str] = []
        cover_data = await self._encode_image_from_url(cover_url)
        if cover_data:
            images.append(f"data:image/jpeg;base64,{cover_data}")
        for path in keyframe_paths:
            frame_data = await self._encode_image_from_file(path)
            if frame_data:
                images.append(f"data:image/jpeg;base64,{frame_data}")

        if not images:
            logger.error("No images available for L2 analysis")
            return None

        image_blocks: List[Dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": img, "detail": "low"}}
            for img in images
        ]
        return await self._run_multimodal(
            instruction=_L2_INSTRUCTION,
            image_content_blocks=image_blocks,
            input_summary=f"L2 analysis of {cover_url} + {len(keyframe_paths)} keyframes",
            metadata={
                "mode": "L2",
                "cover_url": cover_url,
                "keyframe_count": len(keyframe_paths),
            },
            user_id=user_id,
            trigger="visual_analysis_l2",
        )
