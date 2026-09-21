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

Spend is issue-level, and since 3d 第 0 票 both halves are **own spend only**
(a row's own + media, no descendants):

- ``prior`` —— 该议题**全部行**（root + children）的 ``agent_runs.own_cost_cents``
  之和，只排除本 run 的 id，**每个步骤边界重读一次**。Delegate 出去的子 run 带着
  ``issue_id`` 落库，所以它们的花费终于进得了预算；此前那道 root 过滤把它们整个
  挡在门外。
- ``live`` —— 本 run 折出来的 ``own_cents + media_cents``（``spend_of_run``，与写
  ``own_cost_cents`` 列的是同一条表达式），**不含** ``by_child``：子 run 自己那一行
  已经在 ``prior`` 里了。

⚠️ **``prior`` 为什么必须每步重读**（3d 第 0 票终审 I1）：``live`` 只剩自身两道之后，
这一轮里新发生的一切子 agent 花费（同步子 run、后台子 run、Delegate）都**只**落在
它们自己那一行上 —— 也就是只在 ``prior`` 那条聚合里。把 ``prior`` 在 run 开始时冻住，
等于让一个「什么都委派出去」的父 run 在这一轮里对预算完全免疫，直到下一条 run 才被
看见。spec §5 承诺的延迟是「镜像节拍」，不是「一整条 run」。代价是每个 LLM 调用多一条
聚合查询（裁定 10 明示接受）。预算行本身（``budget_cents`` / 议题键 / wrap-up 授权）
仍然每 run 只读一次 —— 它不会在一条 run 中途改变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from app.services.ai.billing.tree_charge import spend_of_run
from app.services.ai.runner.events import emit
from app.services.ai.runner.step_hooks import StepContext, StepDecision

WARN_PCT = 80
HALT_PCT = 100


@dataclass(frozen=True)
class BudgetInfo:
    """What the loader resolves once per run."""

    budget_cents: int
    #: 加载那一刻的 ``prior``。它是**种子值与回退值**，不是每一步用的那个数 ——
    #: 步骤边界上 ``BudgetGateHook`` 会用 ``issue_id`` / ``conversation_id`` 重读
    #: 一次（见模块 docstring 的「为什么必须每步重读」）。
    prior_cents: float
    #: A "Wrap up" grace is on the issue and not spent by an EARLIER run
    #: (unconsumed, or consumed by this very run — a DBOS retry). Claiming it
    #: happens at the halt, not here (``BudgetGateHook._consume``).
    wrap_up: bool = False
    issue_id: Optional[int] = None
    #: 议题的第二条挂靠键（run 经 session 的 conversation 挂上来）。重读 ``prior``
    #: 必须用**与加载时同一组键**，否则同一个议题在第一步与第二步是两个口径。
    conversation_id: Optional[int] = None


BudgetLoader = Callable[[Any], Awaitable[Optional[BudgetInfo]]]
"""recorder → BudgetInfo, or None when the run has no budgeted issue."""

PriorLoader = Callable[[Any, BudgetInfo], Awaitable[float]]
"""(recorder, info) → 此刻该议题除本 run 外的花费。每个步骤边界调一次。"""

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
        conversation_id=int(conversation_id) if conversation_id is not None else None,
    )


async def reload_prior(recorder: Any, info: BudgetInfo) -> float:
    """Default prior loader: the same aggregate ``load_issue_budget`` seeded
    ``prior_cents`` with, re-asked at this step boundary with the same keys.

    同一条 ``spent_cents_for_issue``（``own_cost_cents`` 全行求和，排除本 run）——
    加载与重读读的必须是同一个表达式，否则议题的花费会在第一步与后面几步之间跳一下。

    ``issue_id`` 为 None 时不去问（注入的 loader 可以不填它），直接交回种子值：
    「没有议题键」不是一次读失败，不该走 fail-open 的告警路径。
    """
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    if info.issue_id is None:
        return info.prior_cents
    run_id = getattr(recorder, "run_id", None)
    return float(
        await get_agent_runs_repository().spent_cents_for_issue(
            issue_id=info.issue_id,
            conversation_id=info.conversation_id,
            exclude_run_id=int(run_id) if run_id else None,
        )
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
        refresh: Optional[PriorLoader] = None,
    ):
        self._load = load or load_issue_budget
        self._consume = consume or claim_wrap_up_grace
        self._refresh = refresh or reload_prior
        self._budget_by_run: dict[int, Optional[BudgetInfo]] = {}
        self._prior_by_run: dict[int, float] = {}
        self._prior_unreadable: set[int] = set()
        self._reported: dict[int, set[str]] = {}
        self._grace_steps: dict[int, int] = {}

    async def _budget(self, run_id: int, recorder: Any) -> Optional[BudgetInfo]:
        """预算**行**：每个 run 只读一次。``budget_cents`` / 议题键 / wrap-up 授权
        都不会在一条 run 中途改变，而钱会 —— 钱那一半在 ``_prior``。"""
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

    async def _prior(self, run_id: int, recorder: Any, info: BudgetInfo) -> float:
        """这个议题此刻除本 run 外花了多少 —— **每个步骤边界重读**。

        本轮里子 agent（同步 / 后台 / Delegate）的花费只落在它们自己那一行，也就是
        只在这条聚合里；冻住它就是让委派出去的钱在整条 run 内对门禁隐形。

        读失败沿用「unreadable fails open, loudly, once」那条既有语义：退回上一个读
        到的值（首次失败就是加载时的种子值），每 run 只吵一次，绝不因为一次 DB 抖动
        把每个有预算的议题都停在一个问题上。
        """
        last = self._prior_by_run.get(run_id, info.prior_cents)
        try:
            prior = float(await self._refresh(recorder, info))
        except Exception as exc:  # noqa: BLE001 — fail open, same as the row
            if run_id not in self._prior_unreadable:
                self._prior_unreadable.add(run_id)
                logger.error(
                    f"[budget.gate] prior spend unreadable for run {run_id}; "
                    f"gate keeps using {last}: {exc}"
                )
            return last
        self._prior_by_run[run_id] = prior
        return prior

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
        budget = info.budget_cents
        prior = await self._prior(int(run_id), recorder, info)
        # 这一条 run 自己的两道钱（own + media），**不含** ``by_child`` —— 用的是写
        # ``agent_runs.own_cost_cents`` 那一列的同一条表达式。
        # ⚠️ 不能用 ``cost.spent_cents``（= own + Σby_child + media）：3d 第 0 票起
        # ``prior`` 是该议题**全部行**的 ``own_cost_cents`` 之和（只排除本 run 的 id），
        # 子 run 自己那一行就在里面，再加一遍 ``by_child`` 就是把每个已报回的子 agent
        # 数两遍 —— 父 a + 子 c + 媒体 m 会被读成 a + 2c + m，预算提前触顶。
        live = spend_of_run((getattr(recorder, "views", None) or {}).get("cost")).total
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
    "PriorLoader",
    "WARN_PCT",
    "WRAP_UP_GRACE_STEPS",
    "budget_prompt",
    "claim_wrap_up_grace",
    "load_issue_budget",
    "reload_prior",
]
