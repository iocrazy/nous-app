"""Seam A hook: the issue budget checked at every step boundary (spec §1-⑤).

Phase 1 = record + colour: ``budget_check{action:"warn"}`` once when the
issue's spend crosses 80 % of ``issues.budget_cents``, ``{action:"halt"}``
once at 100 %. Neither stops the run yet — the halt becomes a typed question
(top up / wrap up / cancel) in phase 2. No budget (NULL) → no event, ever.

Spend is issue-level: what earlier runs on the issue already cost (read once
per run from ``agent_runs.cost_cents``) plus this run's live ``cost.spent_cents``
folded from its ``step_end`` events. Root runs only — a sub-agent's spend rolls
up through its parent's ``step_end`` cost, so it must not double-report.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from app.services.ai.runner.events import emit
from app.services.ai.runner.step_hooks import StepContext, StepDecision

WARN_PCT = 80
HALT_PCT = 100

BudgetLoader = Callable[[Any], Awaitable[Optional[tuple[int, float]]]]
"""recorder → (budget_cents, prior_spent_cents) or None when the run has no
budgeted issue. Resolved once per run."""


async def load_issue_budget(recorder: Any) -> Optional[tuple[int, float]]:
    """Default loader: the run's issue (directly, or behind its conversation)
    → ``issues.budget_cents`` + the spend of its earlier runs."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.repositories.issue_repository import issue_repository
    from app.services.ai.runner.inbox import resolve_targets

    targets = await resolve_targets(
        issue_id=getattr(recorder, "issue_id", None),
        conversation_id=getattr(recorder, "conversation_id", None),
    )
    issue_id = next((tid for kind, tid in targets if kind == "issue"), None)
    if issue_id is None:
        return None
    row = await issue_repository.get_by_id(issue_id)
    budget = (row or {}).get("budget_cents")
    if not isinstance(budget, int) or budget <= 0:
        return None
    conversation_id = next(
        (tid for kind, tid in targets if kind == "conversation"), None
    )
    prior = await get_agent_runs_repository().spent_cents_for_issue(
        issue_id=issue_id,
        conversation_id=conversation_id,
        exclude_run_id=(
            int(recorder.run_id) if getattr(recorder, "run_id", None) else None
        ),
    )
    return budget, float(prior)


class BudgetGateHook:
    name = "budget.gate"

    def __init__(self, *, load: Optional[BudgetLoader] = None):
        self._load = load or load_issue_budget
        self._budget_by_run: dict[int, Optional[tuple[int, float]]] = {}
        self._reported: dict[int, set[str]] = {}

    async def _budget(self, run_id: int, recorder: Any) -> Optional[tuple[int, float]]:
        if run_id not in self._budget_by_run:
            self._budget_by_run[run_id] = await self._load(recorder)
        return self._budget_by_run[run_id]

    async def before_llm_call(self, ctx: StepContext) -> StepDecision:
        if ctx.parent_run_id is not None:
            return StepDecision.CONTINUE
        recorder = ctx.recorder
        run_id = getattr(recorder, "run_id", None)
        if recorder is None or run_id is None:
            return StepDecision.CONTINUE
        loaded = await self._budget(int(run_id), recorder)
        if loaded is None:
            return StepDecision.CONTINUE
        budget, prior = loaded
        live = float(
            (getattr(recorder, "views", None) or {}).get("cost", {}).get("spent_cents")
            or 0.0
        )
        spent = round(prior + live, 4)
        pct = spent * 100.0 / budget
        action = "halt" if pct >= HALT_PCT else "warn" if pct >= WARN_PCT else None
        if action is None:
            return StepDecision.CONTINUE
        reported = self._reported.setdefault(int(run_id), set())
        if action in reported:
            return StepDecision.CONTINUE
        reported.add(action)
        await emit(
            recorder,
            "budget_check",
            {
                "spent_cents": spent,
                "budget_cents": budget,
                "pct": round(pct, 1),
                "action": action,
            },
            turn=ctx.turn,
            step=ctx.step,
        )
        return StepDecision.CONTINUE


__all__ = ["BudgetGateHook", "HALT_PCT", "WARN_PCT", "load_issue_budget"]
