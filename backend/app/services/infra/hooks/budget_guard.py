"""BudgetGuard — first PreToolUse hook. Enforces ai_agents.budget_per_run_cents.

Reads ``ctx.accumulated_cost_cents`` (already maintained by RunRecorder
in memory). No DB query at hook time — the per-run budget check is O(1).

M1: per-run only. M2 will extend to per-user-per-day (Layer 2). M4 to
per-team-per-month (Layer 3, paired with Nous Billing).

Loading:
    The budget threshold lives on ``ai_agents.budget_per_run_cents``
    (migration 155). This hook closes over a fetched ``budget_cents``
    value so the AgentRunner doesn't reach into the agent record per
    iteration. Composer or wiring code is responsible for instantiating
    ``BudgetGuardHook(budget_cents=...)`` per run and registering it
    transiently — or registering once and reading the value lazily via
    a callback (see ``budget_loader``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from app.services.infra.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)


# Default fall-back when neither agent nor request specifies a budget.
# Sized so a chatty multi-tool turn at Qwen-Max prices won't trip it
# accidentally, but a pathological 50-iteration run will.
DEFAULT_BUDGET_CENTS = 50.0


@dataclass(frozen=True)
class BudgetGuardHook:
    """PreToolUse hook factory. Bind a per-run budget then register."""

    budget_cents: float = DEFAULT_BUDGET_CENTS
    name: str = "budget_guard"
    priority: int = 20  # Run before CostAuditor (priority 50, post)
    # SCAN/sanitization should be priority < 20 so it filters before
    # we even pay attention to budget.

    async def __call__(self, ctx: HookContext) -> HookResult:
        if self.budget_cents is None:
            return HookResult(decision="continue")

        if ctx.accumulated_cost_cents >= self.budget_cents:
            reason = (
                f"per_run_budget_exceeded "
                f"({ctx.accumulated_cost_cents:.4f} >= {self.budget_cents:.4f} cents) "
                f"agent={ctx.agent_slug} iteration={ctx.iteration}"
            )
            logger.warning("[BudgetGuard] aborting run %s: %s", ctx.run_id, reason)
            return HookResult(decision="abort", abort_reason=reason)

        return HookResult(decision="continue")


# Convenience factory used by AgentRunner wiring code. ``budget_loader``
# is a sync callable that returns the budget for the current ai_agents
# row — typically a closure over the composed prompt or agent record so
# the hook stays cheap (no DB call at hook time).
BudgetLoader = Callable[[], Optional[float]]


def make_budget_guard(
    budget_loader: BudgetLoader,
    *,
    priority: int = 20,
) -> "_LazyBudgetGuard":
    """Wrap a budget-loading callable so the hook reads it lazily.

    Useful when the Hook is registered once at startup but the budget
    differs per run.
    """
    return _LazyBudgetGuard(budget_loader=budget_loader, priority=priority)


@dataclass(frozen=True)
class _LazyBudgetGuard:
    budget_loader: BudgetLoader
    priority: int = 20
    name: str = "budget_guard"

    async def __call__(self, ctx: HookContext) -> HookResult:
        try:
            budget_cents = self.budget_loader()
        except Exception:  # noqa: BLE001 — hook failures must never break the run
            logger.exception("[BudgetGuard] budget_loader failed, assuming unlimited")
            return HookResult(decision="continue")

        if budget_cents is None:
            return HookResult(decision="continue")

        if ctx.accumulated_cost_cents >= budget_cents:
            reason = (
                f"per_run_budget_exceeded "
                f"({ctx.accumulated_cost_cents:.4f} >= {budget_cents:.4f} cents) "
                f"agent={ctx.agent_slug} iteration={ctx.iteration}"
            )
            logger.warning("[BudgetGuard] aborting run %s: %s", ctx.run_id, reason)
            return HookResult(decision="abort", abort_reason=reason)

        return HookResult(decision="continue")


__all__ = [
    "DEFAULT_BUDGET_CENTS",
    "BudgetGuardHook",
    "BudgetLoader",
    "make_budget_guard",
]
