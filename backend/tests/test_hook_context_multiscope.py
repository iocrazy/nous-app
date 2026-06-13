"""M2: HookContext multi-agent scope (Phase 4.5 — canvas plan).

HookContext gains parent_run_id / agent_depth / delegation_chain so
hooks can reason about where in a delegation tree the current tool
call sits (e.g. depth-aware budgets, per-subtree rate limits).

Threading: build_agent_runner_stack appends the current agent's slug
to the inherited chain and hands the scope to both AgentRunner (for
hook contexts) and SubAgentTaskService (so children inherit it).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.ai.runner.agent_runner import AgentRunner
from app.services.infra.hooks import HookContext, HookRegistry, HookResult


class _StubComposed:
    agent_id = uuid4()
    agent_slug = "script_ai"
    model = "qwen3.5-plus"


class _StubSkillTool:
    async def execute(self, args):  # pragma: no cover - not exercised
        return {"ok": True}


# ============================================================
# HookContext defaults (back-compat: M1 hooks keep working)
# ============================================================


def _minimal_context(**over) -> HookContext:
    base = dict(
        run_id="0",
        agent_id=uuid4(),
        agent_slug="script_ai",
        user_id=uuid4(),
        session_id=None,
        tool_name="Skill",
        tool_args={},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
    )
    base.update(over)
    return HookContext(**base)


class TestHookContextDefaults:
    def test_multiscope_fields_default_to_top_of_tree(self) -> None:
        ctx = _minimal_context()
        assert ctx.parent_run_id is None
        assert ctx.agent_depth == 0
        assert ctx.delegation_chain == ()

    def test_multiscope_fields_settable(self) -> None:
        ctx = _minimal_context(
            parent_run_id="123",
            agent_depth=2,
            delegation_chain=("script_ai", "researcher"),
        )
        assert ctx.parent_run_id == "123"
        assert ctx.agent_depth == 2
        assert ctx.delegation_chain == ("script_ai", "researcher")


# ============================================================
# AgentRunner threads its scope into every hook context
# ============================================================


class TestRunnerScopeThreading:
    def test_runner_defaults_to_top_of_tree(self) -> None:
        runner = AgentRunner(adapter=object(), skill_tool=_StubSkillTool())
        ctx = runner._build_context(
            composed=_StubComposed(),
            recorder=None,
            tool_name="Skill",
            args={},
            iteration=1,
        )
        assert ctx.parent_run_id is None
        assert ctx.agent_depth == 0
        assert ctx.delegation_chain == ()

    def test_runner_scope_lands_in_context(self) -> None:
        runner = AgentRunner(
            adapter=object(),
            skill_tool=_StubSkillTool(),
            parent_run_id="987",
            agent_depth=1,
            delegation_chain=("script_ai", "researcher"),
        )
        ctx = runner._build_context(
            composed=_StubComposed(),
            recorder=None,
            tool_name="Skill",
            args={},
            iteration=3,
        )
        assert ctx.parent_run_id == "987"
        assert ctx.agent_depth == 1
        assert ctx.delegation_chain == ("script_ai", "researcher")

    @pytest.mark.asyncio
    async def test_hooks_observe_scope(self) -> None:
        seen: list[HookContext] = []

        async def spy(ctx: HookContext) -> HookResult:
            seen.append(ctx)
            return HookResult(decision="continue")

        registry = HookRegistry()
        registry.register_pre(spy, name="spy", priority=10)

        runner = AgentRunner(
            adapter=object(),
            skill_tool=_StubSkillTool(),
            hooks=registry,
            parent_run_id="42",
            agent_depth=2,
            delegation_chain=("a", "b", "c"),
        )
        ctx = runner._build_context(
            composed=_StubComposed(),
            recorder=None,
            tool_name="Skill",
            args={"skill": "x"},
            iteration=1,
        )
        for entry in registry.get_pre_hooks():
            await runner._safe_invoke_pre(entry.name, entry.hook, ctx)

        assert len(seen) == 1
        assert seen[0].agent_depth == 2
        assert seen[0].delegation_chain == ("a", "b", "c")


# ============================================================
# SubAgentTaskService inherits and extends the chain
# ============================================================


class TestSubAgentChainInheritance:
    def test_service_accepts_delegation_chain(self) -> None:
        from app.services.ai.runner.subagent_task_service import SubAgentTaskService

        svc = SubAgentTaskService(
            caller_agent_id=uuid4(),
            caller_user_id=uuid4(),
            parent_run_id="1",
            agent_depth=1,
            delegation_chain=("script_ai",),
        )
        assert svc.delegation_chain == ("script_ai",)

    def test_service_chain_defaults_empty(self) -> None:
        from app.services.ai.runner.subagent_task_service import SubAgentTaskService

        svc = SubAgentTaskService(
            caller_agent_id=uuid4(),
            caller_user_id=uuid4(),
            parent_run_id=None,
        )
        assert svc.delegation_chain == ()
