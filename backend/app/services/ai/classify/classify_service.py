"""ClassifyService — runs the `classify` agent via AgentRunner.

12-dimension bilingual image classification for the asset library
(IC-port P1-3, 2026-06-12). Same multimodal shape as CaptionService:
downscaled data-URL image block → one agent turn → JSON parse →
``normalize_classification`` flattening.

The agent slug is resolved by ``resolve_classify_provider_config``
(``task_assignment.classification``); the assigned model must be a
vision one — the classify workflow surfaces a clear error otherwise.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
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
from app.services.ai.caption.caption_service import _encode_image_sync
from app.services.ai.classify.normalize import (
    ClassifiedTag,
    normalize_classification,
)
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService

DEFAULT_AGENT_SLUG = "classify"

_INSTRUCTION = (
    "Classify the attached image along your 12 dimensions per your AGENT "
    "spec. Return ONLY the JSON object with no markdown fences."
)


def parse_classification_json(text: str) -> Dict[str, Any]:
    """Parse the agent's JSON payload, tolerating ``` fences."""
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
        logger.warning("[Classify] JSON parse failed on agent output")
        return {}
    return data if isinstance(data, dict) else {}


class ClassifyService:
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
        """Same adapter resolution as Caption/VisualAnalysis services."""
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

    async def classify(
        self,
        *,
        file_path: str,
        user_id: Optional[Any],
        resource_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Optional[List[ClassifiedTag]]:
        """Classify a local image. Returns normalized tags, None on failure.

        An empty list (model answered but nothing usable survived
        normalization) is returned as None too — callers treat both as
        "classification produced nothing".
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
                    logger.warning(f"[Classify] runner error: {result.get('error')}")
                    return None
                tags = normalize_classification(
                    parse_classification_json(result.get("content") or "")
                )
                return tags or None
            except Exception as e:
                logger.error(f"[Classify] bare run failed: {e}")
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
                trigger="asset_classify",
                task_id=task_id,
                model=model or None,
                provider=provider,
                input_summary=f"classify image {file_path.rsplit('/', 1)[-1]}"[:200],
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
                    logger.warning(f"[Classify] runner error: {result.get('error')}")
                    return None
                tags = normalize_classification(parse_classification_json(content))
                return tags or None
        except AgentPausedError as err:
            logger.warning(f"[Classify] agent paused: {err}")
            return None
        except Exception as e:
            logger.error(f"[Classify] run failed: {e}")
            return None
