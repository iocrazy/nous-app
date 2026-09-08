"""Seam A hook: the issue budget checked at every step boundary (spec §1-⑤,
phase 2a §3).

``budget_check{action:"warn"}`` once when the issue's spend crosses 80 % of
``issues.budget_cents``; at 100 % the halt is a typed three-way question:
``budget_check{halt}`` → ``question_asked{kind: budget, options: [Top up,
Wrap up, Cancel], allow_free_text: false}`` → ``ctx.stop("awaiting_input")``.
The answer is handled by ``question_kinds.budget.on_answer`` in the reply
endpoint; "Wrap up" leaves a one-run grace flag
(``issues.execution_state.budget_wrap_up``) that the loader consumes for the
NEXT run, which then passes with ``budget_check{action:"wrap_up"}`` and a
queued steer telling the agent to finish in one step. No budget (NULL) → no
event, ever.

Spend is issue-level: what earlier runs on the issue already cost (read once
per run from ``agent_runs.cost_cents``) plus this run's live ``cost.spent_cents``
folded from its ``step_end`` events. Root runs only — a sub-agent's spend rolls
up through its parent's ``step_end`` cost, so it must not double-report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from app.services.ai.runner.events import emit
from app.services.ai.runner.step_hooks import StepContext, StepDecision

WARN_PCT = 80
HALT_PCT = 100


@dataclass(frozen=True)
class BudgetInfo:
    """What the loader resolves once per run."""

    budget_cents: int
    prior_cents: float
    #: A "Wrap up" grace is on the issue and not spent by an EARLIER run
    #: (unconsumed, or consumed by this very run — a DBOS retry). Claiming it
    #: happens at the halt, not here (``BudgetGateHook._consume``).
    wrap_up: bool = False
    issue_id: Optional[int] = None


BudgetLoader = Callable[[Any], Awaitable[Optional[BudgetInfo]]]
"""recorder → BudgetInfo, or None when the run has no budgeted issue."""

WrapUpConsumer = Callable[[Any, BudgetInfo], Awaitable[bool]]
"""(recorder, info) → True iff this run now holds the one-run grace."""

BUDGET_PROMPT_MAX = 500
#: Step boundaries a "Wrap up" grace lets through before the gate halts again
#: (the steer says "finish in one step"; nothing else enforces it).
WRAP_UP_GRACE_STEPS = 1


def budget_prompt(spent: float, budget: int) -> str:
    return (
        f"Budget exhausted: {spent:g} of {budget} cents spent. Top up the "
        "budget, let the agent wrap up in one step, or cancel the issue."
    )[:BUDGET_PROMPT_MAX]


def _wrap_up_available(flag: Any, run_id: Any) -> bool:
    """The grace is on the issue and an EARLIER run has not spent it."""
    if not isinstance(flag, dict):
        return False
    consumed = flag.get("consumed_by")
    return consumed is None or str(consumed) == str(run_id)


async def load_issue_budget(recorder: Any) -> Optional[BudgetInfo]:
    """Default loader: the run's issue (directly, or behind its conversation)
    → ``issues.budget_cents`` + the spend of its earlier runs + whether a
    wrap-up grace is available. Read-only: the grace is claimed at the halt
    (``claim_wrap_up_grace``), so a run that never reaches 100 % does not
    burn it."""
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
    # NULL = unlimited. 0 is a real budget ("spend nothing more"): any spend
    # is over it.
    if not isinstance(budget, int) or budget < 0:
        return None
    conversation_id = next(
        (tid for kind, tid in targets if kind == "conversation"), None
    )
    run_id = getattr(recorder, "run_id", None)
    prior = await get_agent_runs_repository().spent_cents_for_issue(
        issue_id=issue_id,
        conversation_id=conversation_id,
        exclude_run_id=int(run_id) if run_id else None,
    )
    flag = ((row or {}).get("execution_state") or {}).get("budget_wrap_up")
    return BudgetInfo(
        budget_cents=budget,
        prior_cents=float(prior),
        wrap_up=_wrap_up_available(flag, run_id),
        issue_id=int(issue_id),
    )


async def claim_wrap_up_grace(recorder: Any, info: BudgetInfo) -> bool:
    """Default consumer: the conditional UPDATE in execution_state — one
    winner among racing runs, idempotent for the same run."""
    from app.services.issues.execution_state import claim_budget_wrap_up

    run_id = getattr(recorder, "run_id", None)
    if info.issue_id is None or run_id is None:
        return False
    try:
        return await claim_budget_wrap_up(info.issue_id, run_id)
    except Exception as exc:  # noqa: BLE001 — an unclaimable grace is no grace
        logger.error(
            f"[budget.gate] could not claim budget_wrap_up for issue "
            f"{info.issue_id} run {run_id}: {exc}"
        )
        return False


class BudgetGateHook:
    name = "budget.gate"

    def __init__(
        self,
        *,
        load: Optional[BudgetLoader] = None,
        consume: Optional[WrapUpConsumer] = None,
    ):
        self._load = load or load_issue_budget
        self._consume = consume or claim_wrap_up_grace
        self._budget_by_run: dict[int, Optional[BudgetInfo]] = {}
        self._reported: dict[int, set[str]] = {}
        self._grace_steps: dict[int, int] = {}

    async def _budget(self, run_id: int, recorder: Any) -> Optional[BudgetInfo]:
        if run_id not in self._budget_by_run:
            try:
                self._budget_by_run[run_id] = await self._load(recorder)
            except Exception as exc:  # noqa: BLE001 — decided below, once
                # Explicit decision: an UNREADABLE budget fails open — a DB
                # blip must not park every budgeted issue on a question — but
                # only once per run (cached) and loudly, never re-queried at
                # every step.
                logger.error(
                    f"[budget.gate] budget unreadable for run {run_id}; "
                    f"gate open for this run: {exc}"
                )
                self._budget_by_run[run_id] = None
        return self._budget_by_run[run_id]

    async def before_llm_call(self, ctx: StepContext) -> StepDecision:
        if ctx.parent_run_id is not None:
            return StepDecision.CONTINUE
        recorder = ctx.recorder
        run_id = getattr(recorder, "run_id", None)
        if recorder is None or run_id is None:
            return StepDecision.CONTINUE
        info = await self._budget(int(run_id), recorder)
        if info is None:
            return StepDecision.CONTINUE
        budget, prior = info.budget_cents, info.prior_cents
        live = float(
            (getattr(recorder, "views", None) or {}).get("cost", {}).get("spent_cents")
            or 0.0
        )
        spent = round(prior + live, 4)
        pct = (
            spent * 100.0 / budget
            if budget > 0
            else (float(HALT_PCT) if spent > 0 else 0.0)
        )
        action = "halt" if pct >= HALT_PCT else "warn" if pct >= WARN_PCT else None
        if action is None:
            return StepDecision.CONTINUE
        reported = self._reported.setdefault(int(run_id), set())
        if action == "halt" and "wrap_up" in reported:
            # The grace: WRAP_UP_GRACE_STEPS boundaries through, then the gate
            # halts again (a second question) — "one more step" is a promise
            # the steer alone cannot keep.
            used = self._grace_steps.get(int(run_id), 0) + 1
            self._grace_steps[int(run_id)] = used
            if used <= WRAP_UP_GRACE_STEPS:
                return StepDecision.CONTINUE
        if action in reported:
            # A halt already asked its question this run: the STOP stands
            # (the runner only re-enters on a retried boundary).
            return (
                ctx.stop("awaiting_input")
                if action == "halt"
                else StepDecision.CONTINUE
            )
        if action == "halt" and info.wrap_up and "wrap_up" not in reported:
            if await self._consume(recorder, info):
                action = "wrap_up"
                self._grace_steps[int(run_id)] = 1  # this boundary IS step one
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
        if action != "halt":
            return StepDecision.CONTINUE
        await self._ask(recorder, ctx, spent=spent, budget=budget)
        return ctx.stop("awaiting_input")

    async def _ask(self, recorder: Any, ctx: StepContext, *, spent: float, budget: int):
        """Park the run on the three-way budget question. If the question
        cannot be recorded the run still stops: parking without buttons
        (legacy needs_input) beats spending past the budget."""
        from app.services.ai.runner.question import QuestionNotRecorded, ask_question
        from app.services.ai.runner.question_kinds.budget import BUDGET_OPTIONS

        try:
            await ask_question(
                recorder,
                kind="budget",
                prompt=budget_prompt(spent, budget),
                options=BUDGET_OPTIONS,
                allow_free_text=False,
                turn=ctx.turn,
                step=ctx.step,
            )
        except QuestionNotRecorded as exc:
            logger.error(
                f"[budget.gate] run {getattr(recorder, 'run_id', None)}: budget "
                f"question not recorded ({exc}); parking without options"
            )


__all__ = [
    "BudgetGateHook",
    "BudgetInfo",
    "HALT_PCT",
    "WARN_PCT",
    "WRAP_UP_GRACE_STEPS",
    "budget_prompt",
    "claim_wrap_up_grace",
    "load_issue_budget",
]
