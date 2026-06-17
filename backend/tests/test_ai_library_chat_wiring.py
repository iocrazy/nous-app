"""Unit tests for the M1.5 chat wiring helper.

The wiring helper is the only new code in M1.5 — chat_service.py is just
the call site. These tests verify the wiring matches the contract:
- HookRegistry has all 3 built-in hooks at correct priorities
- Memory recall (Graphiti + Honcho) degrades to empty / None
- Fallback chain wraps the adapter
- BudgetGuard absent when agent.budget_per_run_cents is None

L1 (``agent_memories``) recall has been removed; only Graphiti graph
facts + Honcho user context remain. Both are flag-gated OFF by default,
so an unpatched build returns no graph facts / user context.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat.ai_library_chat_wiring import (
    AgentRunnerStack,
    build_agent_runner_stack,
)


def _agent(*, budget=None, fallbacks=None, model="qwen-max"):
    return {
        "id": str(uuid4()),
        "slug": "script_ai",
        "model": model,
        "budget_per_run_cents": budget,
        "fallback_models": fallbacks or [],
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stack_has_runner_and_memory_fields():
    skill_repo = MagicMock()
    settings = MagicMock()

    stack = await build_agent_runner_stack(
        agent=_agent(),
        skill_repo=skill_repo,
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hello",
        settings=settings,
    )

    assert isinstance(stack, AgentRunnerStack)
    assert stack.runner is not None
    # Graph + Honcho are flag-gated off by default → empty / None.
    assert stack.graph_facts == []
    assert stack.user_context is None
    assert stack.primary_model == "qwen-max"
    assert stack.fallback_chain_active is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_chain_active_when_fallback_models_present():
    settings = MagicMock()
    stack = await build_agent_runner_stack(
        agent=_agent(fallbacks=["qwen-plus", "qwen-turbo"]),
        skill_repo=MagicMock(),
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )
    assert stack.fallback_chain_active is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_guard_registered_when_budget_set():
    """BudgetGuard hook is in the registry only when budget_per_run_cents is non-None."""
    settings = MagicMock()
    stack = await build_agent_runner_stack(
        agent=_agent(budget=50.0),
        skill_repo=MagicMock(),
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )

    pre_names = [e.name for e in stack.runner.hooks.get_pre_hooks()]
    assert "budget_guard" in pre_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_guard_NOT_registered_when_budget_none():
    """No budget configured → no BudgetGuard. Run can spend unlimited."""
    settings = MagicMock()
    stack = await build_agent_runner_stack(
        agent=_agent(budget=None),
        skill_repo=MagicMock(),
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )

    pre_names = [e.name for e in stack.runner.hooks.get_pre_hooks()]
    assert "budget_guard" not in pre_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_hooks_include_cost_auditor_and_memory_harvester():
    settings = MagicMock()
    stack = await build_agent_runner_stack(
        agent=_agent(),
        skill_repo=MagicMock(),
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )

    post_names = [e.name for e in stack.runner.hooks.get_post_hooks()]
    assert "cost_auditor" in post_names
    assert "memory_harvester" in post_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_turn_registry_isolated():
    """Each call to build_agent_runner_stack returns a fresh registry —
    concurrent users must not share hook state."""
    settings = MagicMock()
    stack_a = await build_agent_runner_stack(
        agent=_agent(),
        skill_repo=MagicMock(),
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )
    stack_b = await build_agent_runner_stack(
        agent=_agent(),
        skill_repo=MagicMock(),
        user_id=uuid4(),
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )

    assert stack_a.runner.hooks is not stack_b.runner.hooks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegate_tool_wired_with_caller_context():
    """M2.5 wiring: build_agent_runner_stack must inject a DelegateToolService
    into AgentRunner with the caller's identity baked in. Top-level chat
    turns are always at depth=0 with no parent_run_id."""
    settings = MagicMock()
    user_id = uuid4()
    agent_record = _agent()

    stack = await build_agent_runner_stack(
        agent=agent_record,
        skill_repo=MagicMock(),
        user_id=user_id,
        session_id=uuid4(),
        user_query="hi",
        settings=settings,
    )

    delegate_tool = stack.runner.delegate_tool
    assert delegate_tool is not None
    # Caller context is baked in
    from uuid import UUID as _UUID

    assert delegate_tool.caller_agent_id == _UUID(agent_record["id"])
    assert delegate_tool.caller_user_id == user_id
    # ChatPanel-initiated → top of dispatch tree
    assert delegate_tool.parent_run_id is None
    assert delegate_tool.agent_depth == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_recalls_run_concurrently():
    """The two recalls (graph / honcho) must run concurrently via
    asyncio.gather, not serially. Each gated coroutine blocks until BOTH
    have started — a serial implementation would never reach the second and
    the barrier would time out, failing the test."""
    settings = MagicMock()
    started = 0
    all_started = asyncio.Event()
    lock = asyncio.Lock()

    async def _gate(result):
        nonlocal started
        async with lock:
            started += 1
            if started == 2:
                all_started.set()
        # Serial execution can never satisfy this — only the first recall
        # would run, so the event never sets and wait_for raises.
        await asyncio.wait_for(all_started.wait(), timeout=2.0)
        return result

    async def _graph(**_k):
        return await _gate(["fact"])

    async def _honcho(**_k):
        return await _gate("ctx")

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring._safe_recall_graph_facts",
            side_effect=_graph,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring._safe_recall_honcho_context",
            side_effect=_honcho,
        ),
    ):
        stack = await build_agent_runner_stack(
            agent=_agent(),
            skill_repo=MagicMock(),
            user_id=uuid4(),
            session_id=uuid4(),
            user_query="hi",
            settings=settings,
        )

    assert all_started.is_set()  # both were in flight simultaneously
    assert stack.graph_facts == ["fact"]
    assert stack.user_context == "ctx"


# ─── G1+G5: MCP registry wiring ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_wiring_constructs_mcp_registry_from_user_servers():
    """build_agent_runner_stack pulls user's MCP servers + builds an
    MCPOutboundRegistry that's passed to AgentRunner."""
    from app.repositories.user_mcp_servers_repository import UserMCPServer
    from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack

    user_id = uuid4()
    fake_servers = [
        UserMCPServer(
            id=uuid4(),
            user_id=user_id,
            name="notion",
            url="https://mcp.notion.test/jsonrpc",
            bearer_token="tok123",
            description="Notion workspace",
            enabled=True,
        ),
        UserMCPServer(
            id=uuid4(),
            user_id=user_id,
            name="linear",
            url="https://mcp.linear.test/jsonrpc",
            bearer_token=None,
            description=None,
            enabled=True,
        ),
    ]

    captured_kwargs = {}

    def _capture_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    fake_repo = MagicMock()
    fake_repo.list_for_user = AsyncMock(return_value=fake_servers)

    with (
        patch(
            "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
            return_value=fake_repo,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.LLMFallbackChain",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.AgentRunner",
            side_effect=_capture_runner,
        ),
    ):
        await build_agent_runner_stack(
            agent={
                "id": str(uuid4()),
                "slug": "x",
                "model": "qwen-max",
                "budget_per_run_cents": None,
                "fallback_models": [],
            },
            skill_repo=MagicMock(),
            user_id=user_id,
            session_id=None,
            user_query="hi",
            settings=MagicMock(),
        )

    mcp = captured_kwargs.get("mcp_registry")
    assert mcp is not None, "expected MCPOutboundRegistry to be passed"
    # Two servers registered
    assert sorted(mcp.server_names()) == ["linear", "notion"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_wiring_no_mcp_when_user_has_no_servers():
    """No rows → mcp_registry=None (zero overhead path)."""
    from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack

    captured_kwargs = {}

    def _capture_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    fake_repo = MagicMock()
    fake_repo.list_for_user = AsyncMock(return_value=[])

    with (
        patch(
            "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
            return_value=fake_repo,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.LLMFallbackChain",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.AgentRunner",
            side_effect=_capture_runner,
        ),
    ):
        await build_agent_runner_stack(
            agent={
                "id": str(uuid4()),
                "slug": "x",
                "model": "qwen-max",
                "budget_per_run_cents": None,
                "fallback_models": [],
            },
            skill_repo=MagicMock(),
            user_id=uuid4(),
            session_id=None,
            user_query="hi",
            settings=MagicMock(),
        )
    assert captured_kwargs.get("mcp_registry") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_wiring_mcp_repo_failure_isolated():
    """Exception loading MCP rows → registry stays None, chat continues."""
    from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack

    captured_kwargs = {}

    def _capture_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    fake_repo = MagicMock()
    fake_repo.list_for_user = AsyncMock(side_effect=RuntimeError("db down"))

    with (
        patch(
            "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
            return_value=fake_repo,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.LLMFallbackChain",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.AgentRunner",
            side_effect=_capture_runner,
        ),
    ):
        # Should not raise
        await build_agent_runner_stack(
            agent={
                "id": str(uuid4()),
                "slug": "x",
                "model": "qwen-max",
                "budget_per_run_cents": None,
                "fallback_models": [],
            },
            skill_repo=MagicMock(),
            user_id=uuid4(),
            session_id=None,
            user_query="hi",
            settings=MagicMock(),
        )
    assert captured_kwargs.get("mcp_registry") is None
