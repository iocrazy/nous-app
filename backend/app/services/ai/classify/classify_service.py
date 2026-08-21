"""ClassifyService — runs the `classify` agent via AgentRunner.

12-dimension bilingual image classification for the asset library
(IC-port P1-3, 2026-06-12). Same multimodal shape as CaptionService:
downscaled data-URL image block → one agent turn → JSON parse →
``normalize_classification`` flattening.

The agent slug is resolved by ``resolve_task_ai_config`` (``task_key=
"classification"``, called directly by the workflow's
``resolve_classify_provider`` step — bypassing the
``resolve_task_provider_config`` tuple shim so ``fallback_models`` rides
along); the assigned model must be a vision one — the classify workflow
surfaces a clear error otherwise.

The LLM call runs through :func:`build_fallback_llm` (spec
2026-08-11-batch-llm-fallback / 2026-08-12-batch-fallback-rollout §1-F2)
instead of a bare per-instance adapter, so a primary-model outage fails
over to the resolved agent's ``fallback_models`` pool.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.caption.caption_service import _encode_image_sync
from app.services.ai.classify.normalize import (
    ClassifiedTag,
    normalize_classification,
)
from app.services.ai.prompts.prompt_composer import (
    PromptComposer,
    background_composer_input,
)
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
        # The RESOLVED model — ``resolve_task_ai_config`` writes it into
        # ``provider_config["model"]`` next to the api_key/base_url it resolved
        # FOR that model, so this is the one string that may be dialed. Empty
        # when the resolver produced nothing (bare / smoke path). It gets its
        # own attribute rather than reusing ``self.model`` because that one is
        # per-service: VisualAnalysisService defaults it to a ``"gpt-4o"``
        # cost-estimate placeholder that must never reach the wire.
        self._resolved_model = (self._provider_config.get("model") or "").strip()

    async def classify(
        self,
        *,
        file_path: str,
        user_id: Optional[Any],
        resource_id: Optional[str] = None,
        task_id: Optional[str] = None,
        fallback_models: Optional[list[str]] = None,
    ) -> Optional[List[ClassifiedTag]]:
        """Classify a local image. Returns normalized tags, None on failure.

        An empty list (model answered but nothing usable survived
        normalization) is returned as None too — callers treat both as
        "classification produced nothing".

        ``fallback_models``: platform-preset fallback pool from the
        resolved agent row (spec 2026-08-11-batch-llm-fallback §4),
        threaded into :func:`build_fallback_llm` so a primary-model
        outage fails over instead of erroring the whole classify run.
        """
        data_url = await asyncio.to_thread(_encode_image_sync, file_path)
        if not data_url:
            return None

        composer = PromptComposer(get_agent_repository(), get_skill_repository())
        composed = await composer.compose(
            background_composer_input(
                agent_slug=self.AGENT_SLUG,
                request_instructions=_INSTRUCTION,
                resolved_model=self._resolved_model,
            )
        )

        # 单源收口:解析器给的模型即最终模型。``background_composer_input`` 已经
        # 把它交给 composer 了,这里再把它钉回 ``composed`` —— AgentRunner 从
        # ``composed.model`` 读要拨的号,下面的 fallback 链从这里取
        # ``primary_model``,两条路因此不可能各走各的(#622/#623)。空值(裸 /
        # smoke 路径什么都没解析出来)保留 agent 行自己的模型。
        if self._resolved_model:
            composed = composed.model_copy(update={"model": self._resolved_model})

        from app.services.ai.llm.fallback_wiring import build_fallback_llm

        model_for_chain = composed.model or self.model
        adapter = await build_fallback_llm(
            primary_model=model_for_chain,
            fallback_models=list(fallback_models or []),
            user_provider_config=self._provider_config,
            # self._provider_config is the NARROWED flat single-provider
            # shape ({"model","api_key","base_url"}), not the provider-keyed
            # dict get_adapter_for_user expects — provider_key tells
            # build_fallback_llm to wrap it per-attempt (final-review C1,
            # spec 2026-08-11-batch-llm-fallback). "classification" matches
            # resolve_task_ai_config's own task_key for this module so the
            # pre-resolved platform-catalog gate agrees with the primary
            # model's resolver.
            provider_key=self._provider_key,
            module="classification",
        )
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
        # LLM 类异常(AllModelsFailed/LLMCallError 及其他意外)一律 propagate:
        # classify_asset_workflow 的 tail except 会先 await
        # record_ai_error_code(wf_id, e)(classify_ai_error 落
        # task_tracking.metadata.error_code),再 record_workflow_failure +
        # raise(路线 C rule 4)——吞成 None 会让 workflow 只看到合成
        # RuntimeError,两个机制都够不着真因(本次接线的动机,spec §1-F2)。
