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
- Memory recall (Graphiti graph facts + Honcho user context) happens
  BEFORE prompt composition so it flows into ComposerInput. Recall failure
  degrades to "no memories this turn" — never breaks the chat.
- Fallback chain wraps the adapter at the entry point so retry/fallback
  semantics apply uniformly to every adapter.call inside AgentRunner.
- write_memory_task signature is curried with run-scoped IDs so the
  Celery task knows what to extract.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, Optional
from uuid import UUID

from app.core.config import settings
from app.repositories.agent_memory_repository import get_user_team_ids
from app.services.ai.adapters.factory import get_adapter_for_user
from app.services.ai.llm.llm_fallback_chain import LLMFallbackChain
from app.services.ai.memory import registry as memory_registry
from app.services.ai.memory.agent_memory import recall
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.skills.skill_tool_service import SkillToolService
from app.services.infra.hooks import HookRegistry
from app.services.infra.hooks.budget_guard import BudgetGuardHook
from app.services.infra.hooks.cost_auditor import CostAuditorHook
from app.services.infra.hooks.memory_harvester import MemoryHarvesterHook
from app.services.workforce.delegate_tool import DelegateToolService

logger = logging.getLogger(__name__)

# Overall wall-clock budget for the whole memory-recall step on the chat hot
# path. The per-recall functions already bound their own search/HTTP calls, but
# their *setup* (first-call system_settings load + cold DB/embedder connection
# pools right after a backend restart) falls OUTSIDE those inner timeouts — that
# cold spike once turned the first chat turn into a ~14s hang (2026-06-18). This
# is the belt-and-suspenders ceiling: if recall (both layers, including cold
# setup) can't finish in time, proceed with NO memory rather than stall the
# turn. Memory is best-effort enrichment; warm recall is well under 2s.
MEMORY_RECALL_BUDGET_S = 4.0


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
    # Phase 4 M3: bi-temporal facts from the Graphiti graph (flag-gated;
    # empty when FEATURE_GRAPH_MEMORY is off).
    graph_facts: list[str]
    primary_model: str  # the model that will actually be tried first
    fallback_chain_active: bool
    # Phase 4 L2: Honcho working representation of the user (flag-gated;
    # None when FEATURE_HONCHO_MEMORY is off or the peer has no model yet).
    user_context: Optional[str] = None
    # Phase A: agent-memory recall results (flag-gated on FEATURE_AGENT_MEMORY;
    # [] when off — behavior-neutral until the flag is flipped in prod).
    agent_memory_facts: list[str] = field(default_factory=list)


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
    delegation_chain: tuple[str, ...] = (),
) -> AgentRunnerStack:
    """Construct a fully-wired AgentRunner for one agent turn.

    Two callers:
      - ChatPanel (default): top-of-tree turn, ``parent_run_id=None``,
        ``agent_depth=0``.
      - Worker runtime (M3): inherits ``parent_run_id`` + ``agent_depth+1``
        from the calling agent's run, so Delegate cycle/depth limits
        and cost-rollup-by-tree continue to work.

    ``delegation_chain`` is the ANCESTOR slug chain (root → caller); this
    function appends the current agent's slug and threads the result into
    AgentRunner hook contexts and SubAgentTaskService, so children inherit
    the extended chain (M2 multi-agent scope).

    Steps:
      1. Recall memory (Graphiti graph facts + Honcho user context)
      2. Build per-turn HookRegistry with BudgetGuard + CostAuditor + MemoryHarvester
      3. Wrap adapter in LLMFallbackChain (retry + fallback semantics)
      4. Construct AgentRunner with hooks + Delegate tool
    """
    # Audit #8 coupling: primary_model is the model the chain resolves the
    # primary adapter (endpoint + key) for, and LLMFallbackChain realigns
    # composed.model to it per attempt. ``model_override`` (prompt_composer)
    # is NOT plumbed here today; if it is ever wired through, derive
    # primary_model from it too, else the chain's per-attempt realignment
    # will clobber the override on the primary call.
    primary_model = agent.get("model") or "qwen-max"
    fallback_models: list[str] = list(agent.get("fallback_models") or [])
    budget_cents = agent.get("budget_per_run_cents")

    # ── 1. Memory recall (best-effort, concurrent, hard wall-clock budget) ──
    # The recalls are independent and each exception-safe (degrades to []/None),
    # so we gather them concurrently. graph = FalkorDB search (3s inner cap);
    # honcho = HTTP user-model; agent-memory = DB ranked recall (flag-gated,
    # returns [] immediately when FEATURE_AGENT_MEMORY is off). The whole step
    # is additionally bounded by MEMORY_RECALL_BUDGET_S so cold-start setup
    # (outside the inner timeouts) can never stall the turn.
    from app.services.ai.memory.agent_memory import MemoryContext as _MemCtx

    # Build MemoryContext for agent-memory recall. team_ids deferred to Phase B
    # (empty tuple for Phase A). session_id is a Snowflake BIGINT str from
    # ai_sessions; UUID strings (test fixtures) safely resolve to None.
    _sid: Optional[int] = None
    try:
        _sid = int(str(session_id)) if session_id is not None else None
    except (TypeError, ValueError):
        pass
    _mem_ctx = _MemCtx(
        user_id=str(user_id),
        team_ids=(),
        # project_id: _resolve_session_project runs concurrently inside the
        # gather below (via _safe_recall_graph_facts) — not available here
        # without an extra sequential pre-gather query.  Project-scoped recall
        # lands in Phase B when the resolved value can be shared cleanly.
        project_id=None,
        agent_id=agent.get("id"),
        session_id=_sid,
    )

    try:
        graph_facts, honcho_context, agent_memory_facts = await asyncio.wait_for(
            asyncio.gather(
                _safe_recall_graph_facts(
                    user_id=user_id, user_query=user_query, session_id=session_id
                ),
                _safe_recall_honcho_context(
                    user_id=str(user_id), session_id=session_id
                ),
                _safe_recall_agent_memory(_mem_ctx, user_query),
            ),
            timeout=MEMORY_RECALL_BUDGET_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[memory] recall exceeded %.1fs budget; proceeding without memory",
            MEMORY_RECALL_BUDGET_S,
        )
        graph_facts, honcho_context, agent_memory_facts = [], None, []

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
            # Security gate: if profile evaluation itself errors, BLOCK the
            # tool rather than silently allowing a gated call through.
            fail_closed=True,
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
    #
    # Governance gate: when an admin locks the chat module, skip the user
    # provider lookup entirely.  get_adapter_for_user with an empty user_cfg
    # falls back to platform settings keys (DOUBAO_API_KEY, LLM_API_KEY,
    # etc.) — chat DOES go through get_adapter_for_user, so the env fallback
    # is real and no admin api_key field is needed for the chat module.
    from app.services.ai.governance.ai_governance import get_module_governance as _gov

    _chat_governance = await _gov("chat")
    if not _chat_governance.allowed:
        logger.info(
            "[governance] chat locked by admin for user %s; "
            "skipping user BYO keys — using platform provider keys",
            user_id,
        )
        user_provider_config: dict = {}
    else:
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

    # Chain = ancestors + current agent (slug may be absent in some test
    # fixtures — fall back to id so the chain stays meaningful).
    own_chain = (*delegation_chain, agent.get("slug") or str(agent["id"]))

    # M2-b: parallel fan-out cap from the agent's capability profile.
    # Non-int / missing → default; 0 disables the parallel form (and
    # CapabilityGate blocks the spawn before it reaches the service).
    from app.services.ai.runner.subagent_task_service import DEFAULT_MAX_PARALLEL

    _profile = agent.get("capability_profile")
    _mp = _profile.get("max_parallel_delegates") if isinstance(_profile, dict) else None
    max_parallel = _mp if isinstance(_mp, int) and _mp >= 0 else DEFAULT_MAX_PARALLEL

    skill_tool = SkillToolService(skill_repo)
    skill_tool.subagent_task = SubAgentTaskService(
        caller_agent_id=UUID(agent["id"]),
        caller_user_id=user_id,
        parent_run_id=parent_run_id,
        agent_depth=agent_depth,
        session_id=session_id,
        delegation_chain=own_chain,
        max_parallel=max_parallel,
    )

    runner = AgentRunner(
        adapter=fallback_chain,
        skill_tool=skill_tool,
        hooks=registry,
        delegate_tool=delegate_tool,
        mcp_registry=mcp_registry,
        parent_run_id=parent_run_id,
        agent_depth=agent_depth,
        delegation_chain=own_chain,
    )

    return AgentRunnerStack(
        runner=runner,
        graph_facts=graph_facts,
        primary_model=primary_model,
        fallback_chain_active=bool(fallback_models),
        user_context=honcho_context,
        agent_memory_facts=agent_memory_facts,
    )


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
        # is_enabled() loads the admin-panel (system_settings) config first;
        # reading service.config.enabled directly would see only the env
        # default and keep the panel toggle inert.
        if not await service.is_enabled():
            return []
        # Honour the per-user "inject memory" toggle, mirroring the Honcho
        # read gate (_safe_recall_honcho_context). Opting out of injection
        # must suppress graph facts too, not just the L2 user model.
        from app.services.ai.memory.memory_prefs import get_memory_prefs

        if not (await get_memory_prefs(str(user_id))).inject:
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


async def _safe_recall_honcho_context(
    *,
    user_id: str,
    session_id: Optional[str],
) -> Optional[str]:
    """Best-effort Honcho user-model fetch (Phase 4 L2 read side).

    Pulls the peer's working representation — a pure DB read on the
    Honcho side (the slow LLM-backed dialectic endpoint is deliberately
    NOT used per turn). Workspace mirrors the write path: ``team-{id}``
    when the session carries team context, else the deployment default.
    None when FEATURE_HONCHO_MEMORY is off or anything fails — a Honcho
    outage must never delay or break a chat turn.
    """
    try:
        from app.services.ai.memory.honcho_memory import get_honcho_memory_service
        from app.services.ai.memory.memory_prefs import get_memory_prefs

        provider = await memory_registry.l2_provider()
        if provider is None or not await provider.is_operative():
            return None
        prefs = await get_memory_prefs(user_id)
        if not prefs.inject:
            return None
        workspace = None
        if session_id:
            from app.workflows.write_memory import _resolve_team_workspace

            workspace = await _resolve_team_workspace(str(session_id))
        representation = await provider.get_context(
            user_id=user_id, workspace_id=workspace
        )
        # User-curated "About me" card outranks derived observations —
        # it's the user's own words about themselves.  The card is
        # Honcho-specific CRUD not yet part of the MemoryProvider
        # interface (Phase 1 deferred); keep it on the raw service.
        card: Optional[list[str]] = None
        service = get_honcho_memory_service()
        get_card = getattr(service, "get_peer_card", None)
        if get_card is not None:
            card = await get_card(user_id=user_id, workspace_id=workspace)
        if not card:
            return representation
        card_block = "## About (user-provided)\n" + "\n".join(
            f"- {line}" for line in card
        )
        if not representation:
            return card_block
        return f"{card_block}\n\n{representation}"
    except Exception:  # noqa: BLE001
        logger.exception("[l2] honcho context recall failed; degrading to none")
        return None


async def _safe_recall_agent_memory(
    ctx: Any,
    query: str,
) -> list[str]:
    """Flag-gated agent-memory recall → rendered fact strings.

    Off by default (FEATURE_AGENT_MEMORY=False); returns [] on disabled /
    failure — a memory miss must never break a chat turn.

    When the flag is on, fetches the user's team memberships and rebuilds
    ctx with the populated team_ids so shared memories are included.
    Team-ids fetch is inside the flag gate — zero cost when the flag is off.
    If the team fetch itself raises, recall degrades to owner-only (team_ids=()).
    """
    if not settings.FEATURE_AGENT_MEMORY:
        return []
    try:
        try:
            team_ids = tuple(await get_user_team_ids(str(ctx.user_id)))
            ctx = replace(ctx, team_ids=team_ids)
        except Exception:  # noqa: BLE001 — team fetch failure must not break recall
            logger.warning(
                f"[agent_memory] team-ids fetch failed; falling back to owner-only user={ctx.user_id}"
            )

        hits = await recall(ctx, query, limit=5)
        return [f"{h.kind}: {h.title} — {h.body_md}".strip() for h in hits]
    except Exception:  # noqa: BLE001
        logger.warning("[agent_memory] recall failed; degrading to []")
        return []


async def _resolve_session_project(session_id: Optional[str]) -> Optional[str]:
    """project_id lookup for recall scoping — conversations-only.

    Conversations Phase 3, Task 6 collapsed the compatibility layer: the
    legacy ``ai_sessions`` fallback this used to try after a conversations
    miss is gone (the legacy table itself is dropped in Wave 2). A missing
    row and a row with a NULL ``project_id`` both resolve to None — the
    caller only cares whether a project is in scope.
    """
    if not session_id:
        return None
    try:
        # BIGINT snowflake — asyncpg rejects str binds on int8.
        sid = int(session_id)
    except (TypeError, ValueError):
        return None
    try:
        from app.db import engine as db_engine

        row = await db_engine.fetch_one(
            "SELECT project_id FROM public.conversations WHERE id = :sid",
            {"sid": sid},
        )
        project_id = row.get("project_id") if row else None
        return str(project_id) if project_id else None
    except Exception:  # noqa: BLE001
        return None


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


__all__ = ["AgentRunnerStack", "build_agent_runner_stack"]
