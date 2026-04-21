"""Visual analysis service using OpenAI GPT-4o with multimodal prompts.

Phase 2 PR 2.5: the prompt (IDENTITY / SOUL / AGENT) now lives in the
``analyze`` ``ai_agents`` row, composed via :class:`PromptComposer`. The
multimodal call itself still goes through ``AsyncOpenAI`` directly because
``AgentRunner`` does not yet support image content blocks (that is a Phase
3 concern). This is a *hybrid* migration: DB-driven prompts, native
OpenAI call.

Per-request ``L1`` vs ``L2`` selection is carried through the composer's
``request_instructions`` field. The agent's ``AGENT.md`` documents both
modes; the instruction tells the model which mode to operate in for this
call.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from openai import AsyncOpenAI

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.prompt_composer import ComposerInput, PromptComposer

# Agent slug in the ai_agents table (seeded from backend/seeds/agents/analyze/).
AGENT_SLUG = "analyze"

# Per-request instructions differentiating the two analysis modes.
_L1_INSTRUCTION = (
    "Mode: L1 (cover image only). Follow the L1 section of your AGENT spec. "
    "Return the L1 JSON shape (no content_summary field)."
)
_L2_INSTRUCTION = (
    "Mode: L2 (cover + 3 keyframes at 25% / 50% / 75%). Follow the L2 section "
    "of your AGENT spec. Return the L2 JSON shape including content_summary."
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
    """Service for analyzing video content using AI (hybrid: DB prompt + native OpenAI)."""

    AGENT_SLUG: str = AGENT_SLUG

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, visual analysis will be disabled")
            self.client: Optional[AsyncOpenAI] = None
        else:
            self.client = AsyncOpenAI(api_key=api_key)

        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

    # ── Prompt plumbing (shared) ──────────────────────────────────────

    async def _compose_system_prompt(self, instruction: str) -> str:
        """Fetch the ``analyze`` agent's composed system message from DB.

        Returns only the ``system_message`` text (no tools, no per-turn runtime).
        The caller combines this with the image content in a single user turn.
        """
        composer = PromptComposer(AgentRepository(), SkillRepository())
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=instruction,
            )
        )
        return composed.system_message

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
    def _estimate_cost(usage: Any) -> float:
        """GPT-4o pricing (approx): $2.50 / 1M input, $10 / 1M output."""
        input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        return (input_tokens * 0.0025 + output_tokens * 0.01) / 1000

    # ── Public API ────────────────────────────────────────────────────

    async def analyze_l1(self, cover_url: str) -> Optional[VisualAnalysisResult]:
        """L1 Analysis: cover image only. Cost: ~$0.001 per image."""
        if not self.client:
            logger.warning("OpenAI client not initialized, skipping L1 analysis")
            return None

        image_data = await self._encode_image_from_url(cover_url)
        if not image_data:
            logger.error(f"Failed to encode cover image: {cover_url}")
            return None

        try:
            system_prompt = await self._compose_system_prompt(_L1_INSTRUCTION)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.choices[0].message.content)
            return self._result_from_json(data, self._estimate_cost(response.usage))
        except Exception as e:
            logger.error(f"L1 analysis failed: {e}")
            return None

    async def analyze_l2(
        self, cover_url: str, keyframe_paths: List[str]
    ) -> Optional[VisualAnalysisResult]:
        """L2 Analysis: cover + keyframes. Cost: ~$0.005 per video."""
        if not self.client:
            logger.warning("OpenAI client not initialized, skipping L2 analysis")
            return None

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

        try:
            system_prompt = await self._compose_system_prompt(_L2_INSTRUCTION)
            content: List[Dict[str, Any]] = []
            for img in images:
                content.append(
                    {"type": "image_url", "image_url": {"url": img, "detail": "low"}}
                )
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.choices[0].message.content)
            return self._result_from_json(data, self._estimate_cost(response.usage))
        except Exception as e:
            logger.error(f"L2 analysis failed: {e}")
            return None
