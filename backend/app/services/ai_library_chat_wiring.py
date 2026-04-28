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
from app.services.ai_adapters.factory import get_adapter_for_user
from app.services.embedding_service import EmbeddingService
from app.services.hooks import HookRegistry
from app.services.hooks.budget_guard import BudgetGuardHook
from app.services.hooks.cost_auditor import CostAuditorHook
from app.services.hooks.memory_harvester import MemoryHarvesterHook
from app.services.llm_fallback_chain import LLMFallbackChain
from app.services.memory.retriever import MemoryRetriever
from app.services.prompt_composer import RecalledMemory
from app.services.skill_tool_service import SkillToolService
from app.services.workforce.delegate_tool import DelegateToolService

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
    parent_run_id: Optional[UUID] = None,
    agent_depth: int = 0,
) -> AgentRunnerStack:
    """Construct a fully-wired AgentRunner for one agent turn.

    Two callers:
      - ChatPanel (default): top-of-tree turn, ``parent_run_id=None``,
        ``agent_depth=0``.
      - Worker runtime (M3): inherits ``parent_run_id`` + ``agent_depth+1``
        from the calling agent's run, so Delegate cycle/depth limits
        and cost-rollup-by-tree continue to work.

    Steps:
      1. Recall relevant memories (P0-isolated by user_id)
      2. Build per-turn HookRegistry with BudgetGuard + CostAuditor + MemoryHarvester
      3. Wrap adapter in LLMFallbackChain (retry + fallback semantics)
      4. Construct AgentRunner with hooks + Delegate tool
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

    # MemoryHarvester wired with a dispatch closure that routes through
    # `start_workflow_routed("memory_tasks", ...)`. The routing table
    # decides celery vs shadow vs dbos at fire time. Imports deferred
    # to avoid circular imports with app.tasks at startup.
    import asyncio

    from app.services.dbos_orchestrator import start_workflow_routed
    from app.tasks.memory_tasks import write_memory_task
    from app.workflows.write_memory import write_memory_workflow

    def _memory_signature_factory(
        *,
        run_id: str,
        agent_id: str,
        user_id: str,
        session_id: Optional[str],
        iteration: int,
        tool_name: str,
    ):
        """Return a zero-arg dispatch closure. AgentRunner invokes it
        and continues — fire-and-forget. Wraps the async
        start_workflow_routed call in asyncio.run since this fires from
        a sync hook context."""

        def _fire() -> None:
            asyncio.run(
                start_workflow_routed(
                    "memory_tasks",
                    dbos_workflow_callable=write_memory_workflow,
                    dbos_workflow_kwargs={
                        "agent_id": agent_id,
                        "user_id": user_id,
                        "session_id": session_id,
                        "run_id": run_id,
                        "iteration": iteration,
                        "tool_name": tool_name,
                    },
                    celery_dispatch=lambda: write_memory_task.delay(
                        run_id=run_id,
                        agent_id=agent_id,
                        user_id=user_id,
                        session_id=session_id,
                        iteration=iteration,
                        tool_name=tool_name,
                    ),
                )
            )

        return _fire

    registry.register_post(
        MemoryHarvesterHook(signature_factory=_memory_signature_factory),
        name="memory_harvester",
        priority=80,
    )

    # ── 3. Fallback-wrapped adapter (per-user BYO keys with global fallback) ─
    # Loads user_settings.ai_settings.ai_providers once per turn so
    # Doubao / OpenAI / Claude / Qwen all read the user's BYO key when
    # configured, falling back to global env vars otherwise. M1.5 originally
    # used the global-only get_adapter — that meant any agent on a
    # provider without a global env key (like Doubao here) would 401.
    user_provider_config = await _load_user_provider_config(user_id)

    def _adapter_factory(model: str):
        return get_adapter_for_user(model, user_provider_config, settings)

    fallback_chain = LLMFallbackChain(
        primary_model=primary_model,
        fallback_models=fallback_models,
        adapter_factory=_adapter_factory,
    )

    # ── 4. Delegate tool (M2.5 wiring) ──────────────────────────────
    # ChatPanel uses defaults (parent_run_id=None, depth=0). Worker
    # runtime (M3) passes inherited values so the Delegate tool's
    # depth/cycle protection sees the correct chain position.
    delegate_tool = DelegateToolService(
        caller_agent_id=UUID(agent["id"]),
        caller_user_id=user_id,
        parent_run_id=parent_run_id,
        agent_depth=agent_depth,
    )

    # ── 5. AgentRunner ──────────────────────────────────────────────
    runner = AgentRunner(
        adapter=fallback_chain,
        skill_tool=SkillToolService(skill_repo),
        hooks=registry,
        delegate_tool=delegate_tool,
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


async def _load_user_provider_config(user_id: UUID) -> dict[str, Any]:
    """Read ``user_settings.settings_json.ai_settings.ai_providers`` for
    one user. Returns the providers dict or empty {} on any failure.

    The dict shape is ``{"doubao": {"api_key": "...", "base_url": "..."},
    "qwen": {...}, "openai": {...}, ...}``. Only the provider whose key
    matches the agent's model is actually used by ``get_adapter_for_user``.

    Best-effort: a missing row, malformed JSON, or DB outage degrades to
    "use global env keys only" rather than crashing the chat turn.
    """
    try:
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        result = (
            await client.table("user_settings")
            .select("settings_json")
            .eq("user_id", str(user_id))
            .maybe_single()
            .execute()
        )
        if not result or not result.data:
            return {}
        settings_json = result.data.get("settings_json") or {}
        ai_settings = settings_json.get("ai_settings") or {}
        return ai_settings.get("ai_providers") or {}
    except Exception as err:
        logger.warning(f"[wiring] user_settings lookup failed for {user_id}: {err}")
        return {}


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
