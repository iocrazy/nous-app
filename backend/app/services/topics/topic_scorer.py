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
from app.services.ai.governance.ai_governance import get_module_governance
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.skills.skill_tool_service import SkillToolService

AGENT_SLUG = "topic-scorer"
_VALID_CATEGORIES = {"model", "product", "industry", "paper", "tips"}
_MAX_CONTENT_CHARS = 600  # trim each item's content to bound prompt tokens


class TopicScorerService:
    """Batch-score hotspots through the topic-scorer agent."""

    def _build_composer(self) -> PromptComposer:
        return PromptComposer(get_agent_repository(), get_skill_repository())

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
        """Parse agent output into ``{i: {score,reason,ai_summary,category,tags}}``.

        Defensive: coerces/clamps score, validates category, caps tags, and
        silently drops malformed objects. Never raises.
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
            try:
                score = max(0.0, min(1.0, float(obj.get("score"))))
            except (TypeError, ValueError):
                score = None
            cat = obj.get("category")
            cat = cat if cat in _VALID_CATEGORIES else None
            tags_raw = obj.get("tags")
            tags = (
                [str(t)[:40] for t in tags_raw][:8]
                if isinstance(tags_raw, list)
                else []
            )
            out[idx] = {
                "score": score,
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

        # Resolve admin governance config for this module.
        governance = await get_module_governance("topic_scorer")
        if not governance.model or not governance.api_key_present:
            logger.warning(
                "[topic-scorer] module not configured in admin governance; skipping scoring"
            )
            return {}

        # Build provider adapter from governance config.
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
        composed = composed.model_copy(update={"model": governance.model})
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
