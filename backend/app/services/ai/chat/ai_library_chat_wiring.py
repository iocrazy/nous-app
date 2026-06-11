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

from app.services.ai.adapters import get_adapter
from app.services.ai.adapters.factory import get_adapter_for_user
from app.services.ai.llm.llm_fallback_chain import LLMFallbackChain
from app.services.ai.memory.retriever import MemoryRetriever
from app.services.ai.prompts.prompt_composer import RecalledMemory
from app.services.ai.providers.embedding_service import EmbeddingService
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.skills.skill_tool_service import SkillToolService
from app.services.infra.hooks import HookRegistry
from app.services.infra.hooks.budget_guard import BudgetGuardHook
from app.services.infra.hooks.cost_auditor import CostAuditorHook
from app.services.infra.hooks.memory_harvester import MemoryHarvesterHook
from app.services.workforce.delegate_tool import DelegateToolService

logger = logging.getLogger(__name__)


def _resolve_tool_rate_limit(capability_profile: dict[str, Any]) -> int:
    """Tool-calls-per-minute cap for the RateLimit hook.

    Per-agent ``capability_profile.rate_limit_tool_calls_per_min`` wins;
    ``AGENT_TOOL_CALLS_PER_MIN`` env is the fleet default. 0 (the default)
    disables the brake. Malformed values resolve to 0 — a config typo
    must not brick the agent."""
    override = capability_profile.get("rate_limit_tool_calls_per_min")
    if isinstance(override, int) and override >= 0:
        return override
    raw = os.getenv("AGENT_TOOL_CALLS_PER_MIN", "0")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _llm_total_deadline_s() -> Optional[float]:
    """AI-007: chain-wide wall-time ceiling for LLM retry + fallback.

    Without it, primary retries + each fallback's retries + backoffs can spin
    7-15 min on a flaky upstream. Default 120s bounds the worst case while
    staying well above a normal multi-attempt recovery. ``LLM_TOTAL_DEADLINE_S=0``
    disables it (legacy unbounded behavior).
    """
    raw = os.getenv("LLM_TOTAL_DEADLINE_S", "120")
    try:
        val = float(raw)
    except ValueError:
        return 120.0
    return None if val <= 0 else val


@dataclass(frozen=True)
class AgentRunnerStack:
    """Bundle returned by :func:`build_agent_runner_stack`.

    The chat handler unpacks this and wires it into the existing
    RunRecorder context.
    """

    runner: AgentRunner
    recalled_memories: list[RecalledMemory]
    # Phase 4 M3: bi-temporal facts from the Graphiti graph (flag-gated;
    # empty when FEATURE_GRAPH_MEMORY is off).
    graph_facts: list[str]
    primary_model: str  # the model that will actually be tried first
    fallback_chain_active: bool


async def build_agent_runner_stack(
    *,
    agent: dict[str, Any],
    skill_repo: Any,
    user_id: UUID,
    # session_id / parent_run_id are BIGINT Snowflake ids (mig 231/232) → str.
    session_id: Optional[str],
    user_query: str,
    settings: Any,
    parent_run_id: Optional[str] = None,
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

    graph_facts = await _safe_recall_graph_facts(
        user_id=user_id, user_query=user_query, session_id=session_id
    )

    # ── 2. HookRegistry per-turn ────────────────────────────────────
    registry = HookRegistry()

    # Phase 4.5: tool-call rate brake (Layer-2 budget). Per-agent profile
    # overrides the env default; 0/absent = never registered, zero overhead.
    capability_profile = agent.get("capability_profile") or {}
    rate_limit = _resolve_tool_rate_limit(capability_profile)
    if rate_limit > 0:
        from app.services.infra.hooks.rate_limit import RateLimitHook

        registry.register_pre(
            RateLimitHook(limit_per_min=rate_limit),
            name="rate_limit",
            priority=15,
        )

    if budget_cents is not None:
        registry.register_pre(
            BudgetGuardHook(budget_cents=float(budget_cents)),
            name="budget_guard",
            priority=20,
        )

    # Phase 4.5: per-agent capability gating. Only registered when the
    # agent actually carries a profile — the common (empty) case pays
    # zero per-tool-call overhead.
    if capability_profile:
        from app.services.infra.hooks.capability_gate import CapabilityGateHook

        registry.register_pre(
            CapabilityGateHook(capability_profile),
            name="capability_gate",
            priority=25,
        )

    registry.register_post(
        CostAuditorHook(),
        name="cost_auditor",
        priority=70,
    )

    # MemoryHarvester wired with a dispatch closure that routes through
    # `start_workflow_routed("memory_tasks", ...)`. PR-D7 phase 3:
    # celery_dispatch fallback dropped — routing is all 'dbos' and the
    # legacy write_memory_task Celery wrapper has been deleted.
    #
    # AgentRunner._run_post_hooks (async) invokes this closure ON the event
    # loop, so a bare ``asyncio.run`` would raise "cannot be called from a
    # running event loop" (swallowed by _dispatch_side_effect → memory-harvest
    # silently dropped). Use the loop-safe ``run_async`` helper instead — it
    # falls back to a worker-thread loop when one is already running.
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.tasks.utils import run_async
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
        and continues — fire-and-forget."""

        def _fire() -> None:
            run_async(
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

    # P1-5: pull the per-process ModelHealthRegistry off app.state if
    # available so cooled-down models are skipped on subsequent calls.
    # No registry → legacy linear behavior (try every model in order).
    health_registry = None
    try:
        from app.main import app as _app  # late import to avoid cycle

        health_registry = getattr(_app.state, "model_health", None)
    except Exception:
        health_registry = None

    fallback_chain = LLMFallbackChain(
        primary_model=primary_model,
        fallback_models=fallback_models,
        adapter_factory=_adapter_factory,
        health_registry=health_registry,
        total_deadline_seconds=_llm_total_deadline_s(),
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

    # ── 4.5. MCP outbound registry (G1+G5) ───────────────────────────
    # Per-user MCP server registrations (table user_mcp_servers, mig 194).
    # Each enabled row → MCPServerConfig → registered. AgentRunner.run_turn
    # discovers tools via mcp_registry.all_tools() and routes namespaced
    # tool_calls to mcp_registry.call(...). When user has no servers,
    # registry stays empty (zero overhead — discovery returns []).
    mcp_registry = None
    try:
        from app.agent_framework.mcp_client import MCPServerConfig
        from app.agent_framework.mcp_outbound_registry import MCPOutboundRegistry
        from app.repositories.user_mcp_servers_repository import (
            get_user_mcp_servers_repository,
        )

        mcp_repo = get_user_mcp_servers_repository()
        mcp_rows = await mcp_repo.list_for_user(user_id, only_enabled=True)
        if mcp_rows:
            mcp_registry = MCPOutboundRegistry()
            for row in mcp_rows:
                try:
                    mcp_registry.add_server(
                        MCPServerConfig(
                            name=row.name,
                            url=row.url,
                            bearer_token=row.bearer_token,
                        )
                    )
                except ValueError as exc:
                    # name conflict / invalid name — log + skip the row
                    from loguru import logger as _logger

                    _logger.warning(
                        f"[chat_wiring] skipping MCP server '{row.name}' for user "
                        f"{user_id}: {exc}"
                    )
            from app.agent_framework._metrics_helper import inc_metric

            inc_metric("chat_mcp_registry_built", by=len(mcp_rows))
    except Exception as exc:
        # MCP wiring failures are non-fatal — chat continues without MCP
        from loguru import logger as _logger

        _logger.warning(f"[chat_wiring] MCP registry build skipped: {exc}")
        mcp_registry = None

    # ── 5. AgentRunner ──────────────────────────────────────────────
    # SubAgentTaskService wires the spawn-and-return Task tool that
    # Phase 3b of #199 added. Same caller context as DelegateTool —
    # both share the rate-limit + depth + cycle helpers in
    # workforce/delegate_tool, so the two ways of spawning sub-agents
    # cannot bypass each other's safety net.
    from app.services.ai.runner.subagent_task_service import (
        SubAgentTaskService,
    )

    skill_tool = SkillToolService(skill_repo)
    skill_tool.subagent_task = SubAgentTaskService(
        caller_agent_id=UUID(agent["id"]),
        caller_user_id=user_id,
        parent_run_id=parent_run_id,
        agent_depth=agent_depth,
        session_id=session_id,
    )

    runner = AgentRunner(
        adapter=fallback_chain,
        skill_tool=skill_tool,
        hooks=registry,
        delegate_tool=delegate_tool,
        mcp_registry=mcp_registry,
    )

    return AgentRunnerStack(
        runner=runner,
        recalled_memories=recalled,
        graph_facts=graph_facts,
        primary_model=primary_model,
        fallback_chain_active=bool(fallback_models),
    )


async def _safe_recall_memories(
    *,
    agent_id: UUID,
    user_id: UUID,
    # ai_sessions.id is BIGINT Snowflake (mig 231) → numeric string.
    session_id: Optional[str],
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


async def _safe_recall_graph_facts(
    *,
    user_id: UUID,
    user_query: str,
    session_id: Optional[str] = None,
    limit: int = 5,
) -> list[str]:
    """Best-effort Graphiti fact recall (Phase 4 M3/M7). Searches the
    user's personal group plus — when the session belongs to a project
    — the project group (storyboard characters etc. live there).
    Empty list when the FEATURE_GRAPH_MEMORY flag is off or anything
    fails — graph outages must never delay or break a chat turn."""
    try:
        from app.services.ai.memory.graph_memory import get_graph_memory_service

        service = get_graph_memory_service()
        if not service.config.enabled:
            return []
        group_ids = [f"user-{user_id}"]
        project_id = await _resolve_session_project(session_id)
        if project_id:
            group_ids.append(f"project-{project_id}")
        facts = await service.search(user_query, group_ids=group_ids, limit=limit)
        return [f.fact for f in facts]
    except Exception:  # noqa: BLE001
        logger.exception("[m3] graph fact recall failed; degrading to none")
        return []


async def _resolve_session_project(session_id: Optional[str]) -> Optional[str]:
    """ai_sessions.project_id lookup; None on missing/failure."""
    if not session_id:
        return None
    try:
        # ai_sessions.id is BIGINT — asyncpg rejects str binds on int8.
        sid = int(session_id)
    except (TypeError, ValueError):
        return None
    try:
        from app.db import engine as db_engine

        row = await db_engine.fetch_one(
            "SELECT project_id FROM public.ai_sessions WHERE id = :sid",
            {"sid": sid},
        )
        project_id = row.get("project_id") if row else None
        return str(project_id) if project_id else None
    except Exception:  # noqa: BLE001
        return None


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
    url = os.getenv("REDIS_URL")
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
