"""CaptionService — runs the `caption` agent via AgentRunner.

Reverse-engineers a bilingual generation prompt (EN SD-style + ZH
rendering) from a local image file (IC-port P1-2, 2026-06-12). Mirrors
the multimodal half of VisualAnalysisService: encode the image as a
data-URL content block, compose the agent's IDENTITY/SOUL/AGENT prompt,
run one turn, parse the ``{"en", "zh"}`` JSON.

The agent slug is resolved by ``resolve_caption_provider_config``
(``task_assignment.caption``), so users pick the vision model/provider
in Settings → AI like every other task — the prompt agent and the model
agent are the same one (#622/#623 rule).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import Any, Dict, Optional
from uuid import UUID

from loguru import logger

from app.core.config import settings
from app.repositories.agent_repository import get_agent_repository
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.base import AIAdapter
from app.services.ai.adapters.factory import (
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService

DEFAULT_AGENT_SLUG = "caption"

# Downscale bound before base64-encoding — a 20MB original PNG would blow
# the request payload; 1024px on the long side is plenty for prompt
# reverse-engineering (same bound Infinite-Canvas uses).
MAX_IMAGE_SIDE = 1024
JPEG_QUALITY = 85

_INSTRUCTION = (
    "Reverse-engineer the generation prompt for the attached image per "
    'your AGENT spec. Return ONLY the {"en", "zh"} JSON with no markdown '
    "fences."
)


def _encode_image_sync(file_path: str) -> Optional[str]:
    """Open + downscale + JPEG-encode an image, return a base64 data URL.

    Sync (Pillow) — call via ``asyncio.to_thread``. Returns None on any
    read/decode failure (corrupt upload must not raise here).
    """
    try:
        from PIL import Image

        with Image.open(file_path) as img:
            img = img.convert("RGB")
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.error(f"[Caption] failed to encode image {file_path}: {e}")
        return None


def parse_caption_json(text: str) -> Dict[str, str]:
    """Parse the agent's ``{"en", "zh"}`` payload, tolerating ``` fences.

    Returns a dict containing only the non-empty string sides — empty
    dict when nothing usable came back.
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("[Caption] JSON parse failed on agent output")
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key in ("en", "zh"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


class CaptionService:
    AGENT_SLUG: str = DEFAULT_AGENT_SLUG

    def __init__(
        self,
        provider_key: str = "",
        provider_config: Optional[Dict[str, Any]] = None,
        agent_slug: str = DEFAULT_AGENT_SLUG,
    ) -> None:
        self._provider_key = (provider_key or "").strip()
        self._provider_config: Dict[str, Any] = dict(provider_config or {})
        self.AGENT_SLUG = (agent_slug or DEFAULT_AGENT_SLUG).strip() or (
            DEFAULT_AGENT_SLUG
        )
        self.model = self._provider_config.get("model") or ""

    def _build_adapter(self, model: str) -> AIAdapter:
        """Same adapter resolution as VisualAnalysisService — derive the
        provider key from the model prefix, wrap the user's BYO config,
        fall back to a generic OpenAI-compatible adapter on unknown
        prefixes."""
        provider_key = self._provider_key
        if not provider_key and model:
            try:
                provider_key = provider_key_for_model(model)
            except ValueError:
                provider_key = ""
        if not provider_key:
            return OpenAICompatibleAdapter(
                api_url=self._provider_config.get("base_url", "") or "",
                api_key=self._provider_config.get("api_key", "") or "",
                default_model=model,
            )

        user_cfg_scoped = {
            provider_key: {
                "api_key": self._provider_config.get("api_key", ""),
                "base_url": self._provider_config.get("base_url", "") or "",
                "app_id": self._provider_config.get("app_id", ""),
            }
        }
        try:
            return get_adapter_for_user(model, user_cfg_scoped, settings)
        except ValueError:
            return OpenAICompatibleAdapter(
                api_url=self._provider_config.get("base_url", "") or "",
                api_key=self._provider_config.get("api_key", "") or "",
                default_model=model,
            )

    async def caption(
        self,
        *,
        file_path: str,
        user_id: Optional[Any],
        resource_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """Generate a bilingual prompt from a local image.

        Returns ``{"en": ..., "zh": ...}`` (either side may be absent if
        the model omitted it), or None on failure.
        """
        data_url = await asyncio.to_thread(_encode_image_sync, file_path)
        if not data_url:
            return None

        composer = PromptComposer(get_agent_repository(), get_skill_repository())
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=_INSTRUCTION,
            )
        )

        adapter = self._build_adapter(composed.model or self.model)
        runner = AgentRunner(
            adapter=adapter,
            skill_tool=SkillToolService(get_skill_repository()),
        )
        user_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    }
                ],
            }
        ]

        if user_id is None:
            try:
                result = await runner.run_turn(
                    composed, user_messages=user_messages, recorder=None
                )
                if result.get("error"):
                    logger.warning(f"[Caption] runner error: {result.get('error')}")
                    return None
                return parse_caption_json(result.get("content") or "") or None
            except Exception as e:
                logger.error(f"[Caption] bare run failed: {e}")
                return None

        uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        model = composed.model or self.model
        if self._provider_key:
            provider = self._provider_key
        else:
            try:
                provider = provider_key_for_model(model) if model else "openai"
            except ValueError:
                provider = "openai"

        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=uid,
                trigger="prompt_caption",
                task_id=task_id,
                model=model or None,
                provider=provider,
                input_summary=f"caption image {file_path.rsplit('/', 1)[-1]}"[:200],
                metadata={"resource_id": resource_id},
            ) as recorder:
                result = await runner.run_turn(
                    composed,
                    user_messages=user_messages,
                    recorder=recorder,
                )
                content = result.get("content") or ""
                recorder.set_summaries(output_summary=content[:500])
                if result.get("error"):
                    logger.warning(f"[Caption] runner error: {result.get('error')}")
                    return None
                return parse_caption_json(content) or None
        except AgentPausedError as err:
            logger.warning(f"[Caption] agent paused: {err}")
            return None
        except Exception as e:
            logger.error(f"[Caption] run failed: {e}")
            return None
