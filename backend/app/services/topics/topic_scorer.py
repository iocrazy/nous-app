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

    async def _resolve_candidates(self):
        """Ordered ``[(adapter, model), …]`` the scorer tries in turn (failover).

        Order: (1) admin per-module governance model (explicit override wins);
        (2) EVERY enabled platform Nous ``llm`` model. score_items walks this
        list and uses the first that answers, so one offline endpoint (e.g. a
        self-hosted box on ZeroTier) can't silently kill scoring while reachable
        providers (DeepSeek / Doubao / …) sit unused. Deduped by model, order
        preserved. Empty when nothing is configured. NEVER reads env.
        """
        out: list = []
        gov = await self._governance_adapter()
        if gov is not None:
            out.append(gov)
        out.extend(await self._nous_candidates())
        seen: set[str] = set()
        deduped: list = []
        for adapter, model in out:
            if model in seen:
                continue
            seen.add(model)
            deduped.append((adapter, model))
        return deduped

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

    async def _nous_candidates(self) -> list:
        """``[(adapter, model), …]`` for EVERY enabled platform Nous ``llm``
        model (not just the first) — the failover pool. Empty when Nous is
        disabled for this module or no enabled model resolves."""
        cands: list = []
        try:
            if not await is_nous_allowed("topic_scorer"):
                return cands
            from app.repositories.nous_repository import get_nous_repository

            repo = get_nous_repository()
            for m in await repo.list_enabled("llm"):
                full = await repo.get_by_name(m["name"])
                if full and full.get("base_url") and full.get("api_key"):
                    cands.append(
                        (
                            OpenAICompatibleAdapter(
                                api_url=full["base_url"],
                                api_key=full["api_key"],
                                default_model=full["actual_model"],
                            ),
                            full["actual_model"],
                        )
                    )
        except Exception as e:  # noqa: BLE001 — best-effort, skip on failure
            logger.warning(f"[topic-scorer] Nous resolution failed: {e}")
        return cands

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

        # Resolve the LLM candidates (governance model, then every enabled Nous
        # llm) and try them in order — failover. NEVER env.
        candidates = await self._resolve_candidates()
        if not candidates:
            logger.warning(
                "[topic-scorer] no platform LLM available (Nous off / no enabled "
                "llm model, and no admin governance config); skipping scoring"
            )
            return {}

        # ai_summary length is an admin-tunable runtime knob (topics.scoring
        # config), injected into the request line — not a hardcoded prompt edit.
        # 0 = let the agent's own default ("1-2 句") govern.
        from app.services.topics.scoring import load_scoring_config

        instructions = (
            "Score the following batch of hotspots. Return JSON only, "
            "one object per input item, echoing each item's index `i`."
        )
        try:
            summary_cap = (await load_scoring_config()).summary_max_chars
        except Exception:  # noqa: BLE001 — never block scoring on a config read
            summary_cap = 0
        if summary_cap > 0:
            instructions += f" 每条 ai_summary 控制在 {summary_cap} 个字以内。"

        composer = self._build_composer()
        composed_base = await composer.compose(
            ComposerInput(
                agent_slug=AGENT_SLUG,
                request_instructions=instructions,
            )
        )
        user_messages = [{"role": "user", "content": self.build_user_payload(items)}]

        for adapter, model in candidates:
            runner = AgentRunner(
                adapter=adapter, skill_tool=SkillToolService(get_skill_repository())
            )
            composed = composed_base.model_copy(
                update={"model": model, "max_tokens": _MAX_OUTPUT_TOKENS}
            )
            try:
                result = await runner.run_turn(composed, user_messages=user_messages)
            except Exception as e:  # noqa: BLE001 — try the next candidate
                logger.warning(f"[topic-scorer] model {model} unreachable: {e}")
                continue
            if result.get("error"):
                logger.warning(
                    f"[topic-scorer] model {model} error: {result.get('error')}"
                )
                continue
            try:
                parsed = self._extract_json(result.get("content", "") or "")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[topic-scorer] model {model} JSON parse failed: {e}")
                continue
            return self.normalize_result(parsed)

        # Every candidate failed — surface loudly (ERROR is alert/health-visible,
        # so an offline LLM can't silently kill scoring for days unnoticed).
        logger.error(
            "[topic-scorer] topic scoring DOWN: all %d LLM candidate(s) failed "
            "(check provider reachability / admin governance config)",
            len(candidates),
        )
        return {}
