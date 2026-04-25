"""M1.5 wiring helpers — assembles AgentRunner stack for ChatPanel.

Lives separate from `ai_library_chat_service.py` so the chat handler stays
readable: it calls `build_agent_runner_stack(...)` and gets back everything
hooked up (HookRegistry pre-populated, fallback chain wrapping the adapter,
memory recall pre-fetched).

Design notes:
- HookRegistry is per-chat-turn (NOT shared singleton) so concurrent users
  on the same uvicorn worker can never poison each other's hook state.
  Adversarial-review #8 hardened CostAuditor itself against pollution
  even with a shared singleton, but per-turn instantiation is belt +
  suspenders.
- Memory recall happens BEFORE prompt composition so RecalledMemory list
  flows into ComposerInput. Recall failure degrades to "no memories this
  turn" — never breaks the chat.
- Fallback chain wraps the adapter at the entry point so retry/fallback
  semantics apply uniformly to every adapter.call inside AgentRunner.
- write_memory_task signature is curried with run-scoped IDs so the
  Celery task knows what to extract.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from app.services.agent_runner import AgentRunner
from app.services.ai_adapters import get_adapter
from app.services.embedding_service import EmbeddingService
from app.services.hooks import HookRegistry
from app.services.hooks.budget_guard import BudgetGuardHook
from app.services.hooks.cost_auditor import CostAuditorHook
from app.services.hooks.memory_harvester import MemoryHarvesterHook
from app.services.llm_fallback_chain import LLMFallbackChain
from app.services.memory.retriever import MemoryRetriever
from app.services.prompt_composer import RecalledMemory
from app.services.skill_tool_service import SkillToolService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunnerStack:
    """Bundle returned by :func:`build_agent_runner_stack`.

    The chat handler unpacks this and wires it into the existing
    RunRecorder context.
    """

    runner: AgentRunner
    recalled_memories: list[RecalledMemory]
    primary_model: str  # the model that will actually be tried first
    fallback_chain_active: bool


async def build_agent_runner_stack(
    *,
    agent: dict[str, Any],
    skill_repo: Any,
    user_id: UUID,
    session_id: Optional[UUID],
    user_query: str,
    settings: Any,
) -> AgentRunnerStack:
    """Construct a fully-wired AgentRunner for one chat turn.

    Steps:
      1. Recall relevant memories (P0-isolated by user_id)
      2. Build per-turn HookRegistry with BudgetGuard + CostAuditor + MemoryHarvester
      3. Wrap adapter in LLMFallbackChain (retry + fallback semantics)
      4. Construct AgentRunner with hooks
    """
    primary_model = agent.get("model") or "qwen-max"
    fallback_models: list[str] = list(agent.get("fallback_models") or [])
    budget_cents = agent.get("budget_per_run_cents")

    # ── 1. Memory recall (best-effort) ──────────────────────────────
    recalled = await _safe_recall_memories(
        agent_id=UUID(agent["id"]),
        user_id=user_id,
        session_id=session_id,
        user_query=user_query,
        settings=settings,
    )

    # ── 2. HookRegistry per-turn ────────────────────────────────────
    registry = HookRegistry()

    if budget_cents is not None:
        registry.register_pre(
            BudgetGuardHook(budget_cents=float(budget_cents)),
            name="budget_guard",
            priority=20,
        )

    registry.register_post(
        CostAuditorHook(),
        name="cost_auditor",
        priority=70,
    )

    # MemoryHarvester wired with a Celery signature factory closure.
    # Importing here avoids circular import with app.tasks at startup.
    from app.tasks.memory_tasks import write_memory_task

    def _memory_signature_factory(
        *,
        run_id: str,
        agent_id: str,
        user_id: str,
        session_id: Optional[str],
        iteration: int,
        tool_name: str,
    ):
        return write_memory_task.s(
            run_id=run_id,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            iteration=iteration,
            tool_name=tool_name,
        )

    registry.register_post(
        MemoryHarvesterHook(signature_factory=_memory_signature_factory),
        name="memory_harvester",
        priority=80,
    )

    # ── 3. Fallback-wrapped adapter ─────────────────────────────────
    def _adapter_factory(model: str):
        return get_adapter(model, settings)

    fallback_chain = LLMFallbackChain(
        primary_model=primary_model,
        fallback_models=fallback_models,
        adapter_factory=_adapter_factory,
    )

    # ── 4. AgentRunner ──────────────────────────────────────────────
    runner = AgentRunner(
        adapter=fallback_chain,
        skill_tool=SkillToolService(skill_repo),
        hooks=registry,
    )

    return AgentRunnerStack(
        runner=runner,
        recalled_memories=recalled,
        primary_model=primary_model,
        fallback_chain_active=bool(fallback_models),
    )


async def _safe_recall_memories(
    *,
    agent_id: UUID,
    user_id: UUID,
    session_id: Optional[UUID],
    user_query: str,
    settings: Any,
) -> list[RecalledMemory]:
    """Best-effort memory recall. Empty list on any failure."""
    if not _memory_recall_enabled():
        return []

    try:
        from app.db import get_async_supabase_admin

        client = await get_async_supabase_admin()
    except Exception:  # noqa: BLE001
        logger.exception("[m1.5] supabase admin unavailable; skipping memory recall")
        return []

    embedding_service = EmbeddingService()

    async def embedding_call(text: str):
        return await embedding_service.generate_embedding(text)

    sonnet_call = _build_sonnet_call(settings)
    redis_client = _get_redis_client_or_none()

    retriever = MemoryRetriever(
        supabase_client=client,
        embedding_call=embedding_call,
        sonnet_call=sonnet_call,
        redis_client=redis_client,
    )

    try:
        records = await retriever.recall(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            user_query=user_query,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[m1.5] memory recall failed; degrading to no recall")
        return []

    return [
        RecalledMemory(id=r.id, summary=r.summary, when_to_use=r.when_to_use)
        for r in records
    ]


def _memory_recall_enabled() -> bool:
    """Feature flag — defaults ON in M1.5; can be killed via env if needed."""
    return os.getenv("MEDIAHUB_DISABLE_MEMORY_RECALL", "").lower() not in (
        "1",
        "true",
        "yes",
    )


def _build_sonnet_call(settings: Any):
    """Construct a cheap-model adapter callable for memory ranking.

    Uses Qwen-Turbo (cheapest in our adapter set). If the call fails the
    retriever falls back to top-N salience — never breaks the chat.
    """
    cheap_model = os.getenv("MEDIAHUB_MEMORY_RANKER_MODEL", "qwen-turbo")

    async def _call(prompt: str) -> str:
        from uuid import UUID as _UUID

        from app.schemas.ai_library import ComposedSystemPrompt

        adapter = get_adapter(cheap_model, settings)
        composed = ComposedSystemPrompt(
            agent_id=_UUID(int=0),
            agent_slug="memory_ranker",
            model=cheap_model,
            temperature=0.0,
            max_tokens=512,
            system_message="You rank candidate memories. Output bracketed numbers only.",
            tools=[],
            skill_manifest=[],
            cache_fingerprint="memory_ranker_v1",
        )
        resp = await adapter.call(composed, [{"role": "user", "content": prompt}])
        return resp["choices"][0]["message"].get("content") or ""

    return _call


def _get_redis_client_or_none():
    """Lazy redis import — graceful degradation if redis-py isn't installed
    or REDIS_URL isn't set."""
    url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if not url:
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore
    except ImportError:
        logger.debug("[m1.5] redis-py async not available; cache disabled")
        return None
    try:
        return aioredis.from_url(url, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.exception("[m1.5] redis client init failed; cache disabled")
        return None


__all__ = ["AgentRunnerStack", "build_agent_runner_stack"]
