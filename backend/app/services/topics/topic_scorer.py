"""Topic Scorer — batch-scores hotspots via the ``topic-scorer`` AI Library agent.

Mirrors the thin ScriptAIService pattern: compose the system message from the
``topic-scorer`` ai_agents row (seeded under ``backend/seeds/agents/topic-scorer``),
run one agent turn, parse the JSON array back into per-item enrichment.

The agent produces, per hotspot: ``score`` (0-1), ``reason`` + ``ai_summary``
(中文), ``category`` (one of model/product/industry/paper/tips), and ``tags``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from app.core.config import settings
from app.repositories.agent_repository import get_agent_repository
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.factory import (
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter
from app.services.ai.governance.ai_governance import (
    get_module_governance,
    is_nous_allowed,
)
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.skills.skill_tool_service import SkillToolService
from app.services.topics.scoring import normalize_dims

AGENT_SLUG = "topic-scorer"
_VALID_CATEGORIES = {"model", "product", "industry", "paper", "tips"}
_MAX_CONTENT_CHARS = 600  # trim each item's content to bound prompt tokens
# qwen3-6-35b is a reasoning model: it burns 1-2k tokens on <think> BEFORE the
# JSON answer. With the agent's default 4096 cap, large batches truncated the
# answer mid-array ("Unterminated string"), dropping reason/ai_summary for most
# items. Raise the per-call ceiling so a small batch's full JSON always fits.
# Pairs with workflows.topic_inspiration._SCORE_BATCH_SIZE (kept small).
_MAX_OUTPUT_TOKENS = 8000


class TopicScorerService:
    """Batch-score hotspots through the topic-scorer agent."""

    def _build_composer(self) -> PromptComposer:
        return PromptComposer(get_agent_repository(), get_skill_repository())

    async def _resolve_adapter(self):
        """Resolve (adapter, model) for the background scorer.

        Priority: (1) **admin per-module governance** — an explicitly-configured
        provider for this module wins, so the operator can point topic scoring at
        any reachable platform (DeepSeek/OpenAI/DashScope/…) and instantly route
        around an offline platform default; (2) the platform **Nous** provider's
        default enabled ``llm`` model. Returns ``(None, "")`` when neither is
        configured — scoring is then skipped (additive, never crashes).
        NEVER reads env.
        """
        # 1) Admin per-module governance (explicit override — highest priority).
        gov = await self._governance_adapter()
        if gov is not None:
            return gov

        # 2) Platform Nous provider (default when no module override is set).
        return await self._nous_adapter()

    async def _governance_adapter(self):
        """Adapter from admin per-module governance, or None when the module has
        no configured model/key."""
        governance = await get_module_governance("topic_scorer")
        if not governance.model or not governance.api_key_present:
            return None
        try:
            adapter = get_adapter_for_user(
                governance.model,
                {
                    provider_key_for_model(governance.model): {
                        "api_key": governance.api_key,
                        "base_url": governance.base_url,
                    }
                },
                settings,
            )
        except ValueError:
            adapter = OpenAICompatibleAdapter(
                api_url=governance.base_url,
                api_key=governance.api_key,
                default_model=governance.model,
            )
        return adapter, governance.model

    async def _nous_adapter(self):
        """Adapter from the platform Nous provider's default enabled ``llm``
        model, or ``(None, "")`` when unavailable."""
        try:
            if await is_nous_allowed("topic_scorer"):
                from app.repositories.nous_repository import get_nous_repository

                repo = get_nous_repository()
                llms = await repo.list_enabled("llm")
                if llms:
                    full = await repo.get_by_name(llms[0]["name"])
                    if full and full.get("base_url") and full.get("api_key"):
                        adapter = OpenAICompatibleAdapter(
                            api_url=full["base_url"],
                            api_key=full["api_key"],
                            default_model=full["actual_model"],
                        )
                        return adapter, full["actual_model"]
        except Exception as e:  # noqa: BLE001 — best-effort, skip on failure
            logger.warning(f"[topic-scorer] Nous resolution failed: {e}")
        return None, ""

    def _extract_json(self, text: str) -> Any:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines and lines[-1].strip() == "```":
                cleaned = "\n".join(lines[1:-1])
            else:
                cleaned = "\n".join(lines[1:])
        return json.loads(cleaned)

    def build_user_payload(self, items: list[dict]) -> str:
        """items: [{i, source, title, content}] -> compact JSON for the agent."""
        slim = [
            {
                "i": int(it["i"]),
                "source": (it.get("source") or "")[:80],
                "title": (it.get("title") or "")[:200],
                "content": (it.get("content") or "")[:_MAX_CONTENT_CHARS],
            }
            for it in items
        ]
        return json.dumps(slim, ensure_ascii=False)

    def normalize_result(self, raw: Any) -> dict[int, dict]:
        """Parse agent output into ``{i: {dims,confidence,reason,ai_summary,
        category,tags}}``.

        The agent emits raw per-dimension scores ONLY — the composite ``score``
        is computed in code (see ``scoring.compute_quality``) with the source
        tier, which the agent doesn't see. Defensive: validates dims/category,
        caps tags, drops malformed objects. Never raises.
        """
        out: dict[int, dict] = {}
        if not isinstance(raw, list):
            return out
        for obj in raw:
            if not isinstance(obj, dict) or "i" not in obj:
                continue
            try:
                idx = int(obj["i"])
            except (TypeError, ValueError):
                continue
            dims = normalize_dims(obj.get("dims"))
            try:
                confidence = max(0.0, min(1.0, float(obj.get("confidence"))))
            except (TypeError, ValueError):
                confidence = None
            cat = obj.get("category")
            cat = cat if cat in _VALID_CATEGORIES else None
            tags_raw = obj.get("tags")
            tags = (
                [str(t)[:40] for t in tags_raw][:8]
                if isinstance(tags_raw, list)
                else []
            )
            out[idx] = {
                "dims": dims,
                "confidence": confidence,
                "reason": (
                    (str(obj.get("reason")).strip() or None)
                    if obj.get("reason")
                    else None
                ),
                "ai_summary": (
                    (str(obj.get("ai_summary")).strip() or None)
                    if obj.get("ai_summary")
                    else None
                ),
                "category": cat,
                "tags": tags,
            }
        return out

    async def score_items(
        self, items: list[dict], *, user_id: Optional[str] = None
    ) -> dict[int, dict]:
        """Score one batch. Returns ``{i: enrichment}``; empty dict on failure."""
        if not items:
            return {}

        # Resolve the LLM. Background system agents (no user) get their model
        # from the platform's Nous provider first, then admin per-module
        # governance. NEVER env.
        adapter, model = await self._resolve_adapter()
        if adapter is None:
            logger.warning(
                "[topic-scorer] no platform LLM available (Nous off / no enabled "
                "llm model, and no admin governance config); skipping scoring"
            )
            return {}

        runner = AgentRunner(
            adapter=adapter, skill_tool=SkillToolService(get_skill_repository())
        )

        composer = self._build_composer()
        composed = await composer.compose(
            ComposerInput(
                agent_slug=AGENT_SLUG,
                request_instructions=(
                    "Score the following batch of hotspots. Return JSON only, "
                    "one object per input item, echoing each item's index `i`."
                ),
            )
        )
        composed = composed.model_copy(
            update={"model": model, "max_tokens": _MAX_OUTPUT_TOKENS}
        )
        user_messages = [{"role": "user", "content": self.build_user_payload(items)}]
        result = await runner.run_turn(composed, user_messages=user_messages)
        if result.get("error"):
            logger.warning(f"[topic-scorer] runner error: {result.get('error')}")
            return {}
        try:
            parsed = self._extract_json(result.get("content", "") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[topic-scorer] JSON parse failed: {e}")
            return {}
        return self.normalize_result(parsed)
