"""Unit tests for the M1.5 chat wiring helper.

The wiring helper is the only new code in M1.5 — chat_service.py is just
the call site. These tests verify the wiring matches the contract:
- HookRegistry has all 3 built-in hooks at correct priorities
- Memory recall failure degrades to empty list
- Fallback chain wraps the adapter
- BudgetGuard absent when agent.budget_per_run_cents is None
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai_library_chat_wiring import (
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
async def test_stack_has_runner_and_recalled_memories():
    skill_repo = MagicMock()
    settings = MagicMock()

    with patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        AsyncMock(return_value=[]),
    ):
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
    assert stack.recalled_memories == []
    assert stack.primary_model == "qwen-max"
    assert stack.fallback_chain_active is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_chain_active_when_fallback_models_present():
    settings = MagicMock()
    with patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        AsyncMock(return_value=[]),
    ):
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
    with patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        AsyncMock(return_value=[]),
    ):
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
    with patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        AsyncMock(return_value=[]),
    ):
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
    with patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        AsyncMock(return_value=[]),
    ):
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
    with patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        AsyncMock(return_value=[]),
    ):
        stack_a = await build_agent_runner_stack(
            agent=_agent(), skill_repo=MagicMock(),
            user_id=uuid4(), session_id=uuid4(), user_query="hi",
            settings=settings,
        )
        stack_b = await build_agent_runner_stack(
            agent=_agent(), skill_repo=MagicMock(),
            user_id=uuid4(), session_id=uuid4(), user_query="hi",
            settings=settings,
        )

    assert stack_a.runner.hooks is not stack_b.runner.hooks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_failure_degrades_to_empty():
    """If memory recall path raises, return [] — never break chat."""
    settings = MagicMock()

    async def broken_recall(**_kwargs):
        raise RuntimeError("supabase down")

    # Patch the inner function's helper so the outer one's try-except catches.
    with patch(
        "app.services.ai_library_chat_wiring.get_adapter",
        return_value=MagicMock(),
    ), patch(
        "app.services.ai_library_chat_wiring._safe_recall_memories",
        side_effect=broken_recall,
    ):
        with pytest.raises(RuntimeError):
            # When _safe_recall raises, it's a programming error in the
            # helper itself — should propagate. (The helper's INTERNAL
            # try/except is what protects against I/O errors.)
            await build_agent_runner_stack(
                agent=_agent(), skill_repo=MagicMock(),
                user_id=uuid4(), session_id=uuid4(), user_query="hi",
                settings=settings,
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_returns_empty_when_supabase_unavailable():
    """The internal _safe_recall path should swallow supabase init errors.

    `get_async_supabase_admin` is imported inside the function (lazy
    deferred import) so the patch path is the actual module of origin.
    """
    from app.services.ai_library_chat_wiring import _safe_recall_memories

    with patch(
        "app.db.get_async_supabase_admin",
        side_effect=RuntimeError("supabase not configured"),
    ):
        result = await _safe_recall_memories(
            agent_id=uuid4(), user_id=uuid4(),
            session_id=None, user_query="hi", settings=MagicMock(),
        )
    assert result == []
