"""Unit tests for BudgetGuard hook."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.services.hooks import HookContext
from app.services.hooks.budget_guard import (
    DEFAULT_BUDGET_CENTS,
    BudgetGuardHook,
    make_budget_guard,
)


def _ctx(accumulated_cost_cents: float, iteration: int = 1) -> HookContext:
    return HookContext(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_id=UUID("00000000-0000-0000-0000-000000000002"),
        agent_slug="script_ai",
        user_id=UUID("00000000-0000-0000-0000-000000000003"),
        session_id=None,
        tool_name="Skill",
        tool_args={"skill": "script-outline"},
        accumulated_prompt_tokens=100,
        accumulated_completion_tokens=200,
        accumulated_cost_cents=accumulated_cost_cents,
        iteration=iteration,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_guard_under_budget_continues():
    hook = BudgetGuardHook(budget_cents=50.0)
    result = await hook(_ctx(accumulated_cost_cents=10.0))
    assert result.decision == "continue"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_guard_at_threshold_aborts():
    """Boundary test: at == threshold should abort (>= comparison)."""
    hook = BudgetGuardHook(budget_cents=50.0)
    result = await hook(_ctx(accumulated_cost_cents=50.0))
    assert result.decision == "abort"
    assert result.abort_reason is not None
    assert "per_run_budget_exceeded" in result.abort_reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_guard_over_budget_aborts():
    hook = BudgetGuardHook(budget_cents=50.0)
    result = await hook(_ctx(accumulated_cost_cents=100.0))
    assert result.decision == "abort"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_guard_default_threshold_is_50_cents():
    assert DEFAULT_BUDGET_CENTS == 50.0
    hook = BudgetGuardHook()  # uses default
    assert hook.budget_cents == 50.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lazy_budget_guard_with_loader_returning_value():
    """make_budget_guard wires a callable; it should read it per call."""
    current_budget = [100.0]

    def loader():
        return current_budget[0]

    hook = make_budget_guard(loader)
    result = await hook(_ctx(accumulated_cost_cents=50.0))
    assert result.decision == "continue"

    # Mutate budget mid-run; lazy loader picks up the new value.
    current_budget[0] = 30.0
    result = await hook(_ctx(accumulated_cost_cents=50.0))
    assert result.decision == "abort"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lazy_budget_guard_loader_returning_none_means_unlimited():
    hook = make_budget_guard(lambda: None)
    result = await hook(_ctx(accumulated_cost_cents=999.0))
    assert result.decision == "continue"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lazy_budget_guard_loader_exception_falls_back_to_continue():
    """Loader bug must not crash the run."""

    def broken_loader():
        raise RuntimeError("budget config corrupt")

    hook = make_budget_guard(broken_loader)
    result = await hook(_ctx(accumulated_cost_cents=999.0))
    assert result.decision == "continue"
