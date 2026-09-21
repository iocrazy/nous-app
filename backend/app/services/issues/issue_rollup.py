"""``issue.rollup`` — one issue's progress across all its runs (spec §1-② view
family, read-time). Pure ``compute_rollup`` + an async ``load_rollup`` that
gathers its inputs; the endpoint is ``GET /issues/{id}/progress``.

Phase is derived from the RUNS, not from ``issues.execution_state`` — the
MH-1 drift ("running · turn 7 · 2474h" with no running run) is exactly what
trusting the decoration produced. Priority:
``paused > waiting_input > running > blocked > done > idle``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, Optional

from loguru import logger

from app.services.billing.run_tree_points import charged_points_for_run_trees
from app.services.issues.origin_resolvers import resolve_origin

TERMINAL_ISSUE = frozenset({"done", "cancelled", "closed"})
FAILED_RUN = frozenset({"failed", "heartbeat_lost"})


def _view(run: dict[str, Any]) -> dict[str, Any]:
    meta = run.get("metadata_json") or {}
    return meta.get("view") or {} if isinstance(meta, dict) else {}


def _cost(run: dict[str, Any]) -> dict[str, Any]:
    meta = run.get("metadata_json") or {}
    return meta.get("cost") or {} if isinstance(meta, dict) else {}


def _run_cents(run: dict[str, Any]) -> float:
    """**回退**口径：这一行自己那一列（``agent_runs.cost_cents`` = 自身 + 已报到的
    后代），running 的行改读它自己的实时视图。

    3d 第 0 票终审 I2 起，``runs[].cost_cents`` 的正路是 ``tree_cost_cents`` 那张
    按树求和的图（见 ``_row_cents``）。这个函数只剩两个住处：

    1. **running 那一行** —— 库里那一列**不是**空的：``run_recorder`` 的 ``mirror_stmt``
       每次镜像都把 ``own_cost_cents`` 一起写下去，所以树总额对一条在跑的 run 也是
       有值的、只是停在**最后一次镜像**。选视图是因为它更新：内存里的折算比最后那次
       镜像新，而且这样 ``runs[<running>].cost_cents`` 与同一份 payload 里的
       ``current_run.cost`` 逐字是同一个数 —— 一个面上两个数字打架比落后一拍更糟。
    2. **树图里没有这个 root 的键** —— 一次读空不该把一行的钱显示成 0，退回这一行
       自己那一列（低报总好过凭空归零）。

    ⚠️ 两条都不是议题的花费。议题那个数由 ``own_cost_cents_for_issue_runs`` 在库里
    求和 —— 在这里把每行加起来只会得到 root 们的树总额，把 Delegate 子 run 漏在
    外面（3d 第 0 票）。
    """
    if run.get("status") == "running":
        return float(_cost(run).get("spent_cents") or 0.0)
    try:
        return float(run.get("cost_cents") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _row_cents(run: dict[str, Any], tree_cost_cents: dict[str, float]) -> float:
    """``runs[].cost_cents`` —— 这一行**那棵树**的总额（Σ ``own_cost_cents``）。

    Budget 格（``spent``）是该议题全部行的 ``own_cost_cents`` 之和；下面这些行若还
    在显示折叠列 ``agent_runs.cost_cents``，两个数就对不上账 —— 委派链上没报回父行
    的子 run 只进得了上面那个总额，进不了任何一行。T7 那棵树实测 ¢35.75 vs 逐行加
    起来 ¢15.50，同一个面上两个说法。

    取数与 ``/ai-library/runs/costs``、``done`` 帧共用 ``tree_cost_cents``，所以「这
    次回合花了多少」在三个面上是同一个数。
    """
    if run.get("status") == "running":
        return _run_cents(run)
    cents = tree_cost_cents.get(str(run.get("id")))
    return _run_cents(run) if cents is None else float(cents)


def _current_run(runs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """这个 issue 此刻在跑的那条 run，没有就是 None。

    ``derive_phase`` / ``compute_rollup`` / ``load_rollup`` 必须挑出**同一条**
    run —— 前两个拿它定相位与填 ``current_run``，第三个拿它去问 transcript 水位。
    同一个 next(...) 抄三遍，就是给它们三条分叉的机会。"""
    return next((r for r in runs if r.get("status") == "running"), None)


def derive_phase(issue: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    state = issue.get("execution_state") or {}
    if not isinstance(state, dict):
        state = {}
    current = _current_run(runs)
    latest = runs[0] if runs else None
    if issue.get("paused_at"):
        return "paused"
    if (
        "awaiting_input" in state
        or state.get("agent_outcome") == "needs_input"
        or (current is not None and _view(current).get("phase") == "waiting_input")
        or (
            latest is not None
            and (_view(latest).get("ended") or {}).get("reason") == "awaiting_approval"
        )
    ):
        return "waiting_input"
    if current is not None:
        return "running"
    if issue.get("status") in TERMINAL_ISSUE:
        return "done"
    if issue.get("status") == "blocked" or (
        latest is not None and latest.get("status") in FAILED_RUN
    ):
        return "blocked"
    return "idle"


#: 一个还没跑过 run 的议题也得有完整形状——前端无条件读这八个键。
EMPTY_EFFICIENCY: dict[str, Any] = {
    "runs": 0,
    "steps": 0,
    "tool_calls": 0,
    "tool_errors": 0,
    "deliverables": 0,
    "avg_run_ms": None,
    "turn_end_reasons": {},
}


def compute_rollup(
    issue: dict[str, Any],
    runs: list[dict[str, Any]],
    sub_issues: list[dict[str, Any]],
    inbox_pending: int,
    origin: dict[str, Any],
    *,
    spent_cents: float,
    tree_cost_cents: dict[str, float],
    now: Optional[dt.datetime] = None,
    last_seq: Optional[int] = None,
    efficiency: Optional[dict[str, Any]] = None,
    charged_points: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """``runs`` newest first, root runs only.

    ``spent_cents`` 是这个议题的花费，由调用方从
    ``agent_runs_repository.own_cost_cents_for_issue_runs`` 取（``load_rollup``
    负责）。**没有默认值**，因为唯一合理的默认是 0，而一个忘了传的调用方会让预算格
    永远显示「没花钱」——预算门禁那边同时在拦人，两个面各说一套。逐行加
    ``_run_cents`` 也不行：那样只数得到 root 的树总额，Delegate 子 run 不在其中。

    ``tree_cost_cents`` 是 ``{root run id: 这棵树的 Σ own_cost_cents}``，由调用方从
    ``agent_runs_repository.tree_cost_cents`` 取。**同样没有默认值**，理由同上：给个
    ``{}`` 就是让每一行悄悄退回折叠列，而 Budget 格已经换成全行求和了——那正是
    终审 I2 那笔对不上的账（¢35.75 的格子下面躺着加起来 ¢15.50 的几行）。
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    current = _current_run(runs)
    spent = round(float(spent_cents), 4)
    budget = issue.get("budget_cents")
    # Same reading as BudgetGateHook: NULL = unlimited (no pct), 0 = a real
    # budget meaning "spend nothing more" — any spend is 100 % over it. Before
    # 2026-09-06 the `budget > 0` guard left 0 looking unlimited (pct None,
    # state ok) while the hook on the same run had already recorded
    # budget_check{halt, pct 100}: two answers for one number.
    pct: Optional[int] = None
    if isinstance(budget, int) and budget >= 0:
        if budget > 0:
            pct = round(spent * 100 / budget)
        else:
            pct = 100 if spent > 0 else 0
    state = "ok"
    if pct is not None:
        state = "over" if pct >= 100 else "warn" if pct >= 80 else "ok"
    done_children = sum(1 for s in sub_issues if s.get("status") in TERMINAL_ISSUE)
    # 3c §3.3：计数按全体 run 求和（root + children），因为计数是每个 run 的自身量。
    # 3d 第 0 票起分子 ``spent`` 也是全体求和（``own_cost_cents`` 每行只记自身），
    # 所以 ``¢/output`` 的分子分母终于同一批 run —— 此前分子是 root 的树总额、分母
    # 含子 agent 干的活，委派越多单价越虚高。
    eff = {**EMPTY_EFFICIENCY, **(efficiency or {})}
    # 浅拷贝只复制顶层：没带 turn_end_reasons 的调用方会拿到 EMPTY_EFFICIENCY 里
    # 那一个 dict 本身，谁改一下就污染了之后每一个议题。重新包一层。
    eff["turn_end_reasons"] = dict(eff.get("turn_end_reasons") or {})
    delivered = int(eff.get("deliverables") or 0)
    eff["cost_per_deliverable_cents"] = (
        round(spent / delivered, 4) if delivered > 0 else None
    )
    points = charged_points or {}
    return {
        "issue_id": str(issue["id"]),
        "status": issue.get("status"),
        "phase": derive_phase(issue, runs),
        "paused_at": issue.get("paused_at"),
        "current_run": (
            {
                "id": str(current["id"]),
                "status": current.get("status"),
                "started_at": current.get("started_at"),
                "model": current.get("model"),
                "view": _view(current),
                "cost": _cost(current),
                # 该 run 的 transcript 水位，轮询边沿用它跟 WS 的 done 帧去重
                # （3b §4）；读不到就是 None——0 会把最新的一帧当最旧的丢掉。
                "last_seq": last_seq,
            }
            if current
            else None
        ),
        "runs": [
            {
                "id": str(r["id"]),
                "status": r.get("status"),
                "started_at": r.get("started_at"),
                "ended_at": r.get("ended_at"),
                "model": r.get("model"),
                "error_code": r.get("error_code"),
                "cost_cents": _row_cents(r, tree_cost_cents),
                "ended": _view(r).get("ended"),
                "step": _view(r).get("step"),
                "charged_points": points.get(str(r["id"])),
            }
            for r in runs
        ],
        "sub_issues": {
            "total": len(sub_issues),
            "done": done_children,
            "items": [
                {
                    "id": str(s["id"]),
                    "identifier": s.get("identifier"),
                    "title": s.get("title"),
                    "status": s.get("status"),
                }
                for s in sub_issues
            ],
        },
        "inbox_pending": int(inbox_pending),
        "efficiency": eff,
        "budget": {
            "budget_cents": budget if isinstance(budget, int) else None,
            "spent_cents": spent,
            "pct": pct,
            "state": state,
        },
        "origin": origin,
        "execution_state": state_dict(issue),
        "computed_at": now,
    }


def state_dict(issue: dict[str, Any]) -> dict[str, Any]:
    s = issue.get("execution_state")
    return s if isinstance(s, dict) else {}


async def load_rollup(issue: dict[str, Any]) -> dict[str, Any]:
    """Gather runs / children / inbox / origin and fold them."""
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.repositories.issue_repository import issue_repository

    issue_id = int(issue["id"])
    session_id = issue.get("ai_session_id")
    # 一条 run 挂到议题上有两条路（直接 issue_id，或经 session 的 conversation_id）。
    # 行与钱必须用**同一组键**去找：只按 issue_id 求和会让「只走会话键」的 run 出现在
    # 列表里却不进 Budget 格，而三处 docstring 都写着口径一致。
    conversation_id = int(session_id) if session_id else None
    runs = await get_agent_runs_repository().list_for_issue(
        issue_id=issue_id,
        conversation_id=conversation_id,
    )
    children = await issue_repository.list_children(issue_id)
    pending = await get_agent_run_inbox_repository().pending_count(
        target_kind="issue", target_id=issue_id
    )
    origin = await resolve_origin(issue)

    # 效率账与积分账互不依赖，串行只是白等一个往返 —— 这个端点是被轮询的。
    async def _charged() -> dict[str, float]:
        """积分账读失败只空掉这一个字段，不带走整个 rollup。

        ``runs`` 是 root-only（``list_for_issue`` 明写 ``parent_run_id IS NULL``），
        而扣费是**逐 run** 发生的，所以问的是「以这些 root 为根的整棵树各扣了多少」
        ——3c 终审 I2：此前只取 root 自己那一条流水，真栈一次回合 6 条、余额 −6，
        界面显示 ◇ 1.00。取数与气泡、``done`` 帧共用
        ``charged_points_for_run_trees``，三处必须是同一个数。

        仓库层一律 raise —— 另一个读方 ``/ai-library/runs/costs`` 要靠它答 503，
        而不是把一个真花了钱的 run 显示成免费。降级的责任因此落在各消费方；这里是
        被轮询的驾驶舱，一个字段读不到不该让进度、子议题、收件箱计数一起消失。

        ⚠️ 必须自己 catch：``asyncio.gather`` 默认任何一个协程抛出就整体抛出。
        用 ``return_exceptions=True`` 则会连带吞掉效率账那条的失败，那不是想要的。
        """
        try:
            return await charged_points_for_run_trees([r["id"] for r in runs])
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"[issue_rollup] charged points read failed for {issue_id}: {e}"
            )
            return {}

    # 花费问库，不是把 ``runs`` 加起来：``runs`` 是 root-only，而钱要算上 Delegate
    # 出去的子 run（3d 第 0 票）。读失败**故意**往上抛 —— 预算格是个门禁面，把读不到
    # 显示成 0 就是说「随便花」。效率账那条自己回 {}，积分那条自己 catch，只有钱那
    # 两条有资格带走整个 rollup。
    #
    # 两条钱各答一个问题，缺一不可：``spent`` 是**议题**的总额（Budget 格），
    # ``tree_cost`` 是**每棵树**的总额（下面那几行各自那一格）。行若还读折叠列
    # ``agent_runs.cost_cents``，格子与它下面的行就对不上账（终审 I2）。
    efficiency, charged, spent, tree_cost = await asyncio.gather(
        get_agent_runs_repository().efficiency_for_issue(issue_id),
        _charged(),
        get_agent_runs_repository().own_cost_cents_for_issue_runs(
            issue_id, conversation_id
        ),
        get_agent_runs_repository().tree_cost_cents([int(r["id"]) for r in runs]),
    )
    current = _current_run(runs)
    last_seq = (
        await get_agent_runs_repository().last_transcript_seq(int(current["id"]))
        if current
        else None
    )
    return compute_rollup(
        issue,
        runs,
        children,
        pending,
        origin,
        spent_cents=spent,
        tree_cost_cents=tree_cost,
        last_seq=last_seq,
        efficiency=efficiency,
        charged_points=charged,
    )


__all__ = ["compute_rollup", "derive_phase", "load_rollup"]
