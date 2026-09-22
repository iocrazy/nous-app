"""Read-side queries for the AI Usage panel (W3c).

Range/summary queries read ONLY the ai_usage_hourly rollup cache (never raw
agent_runs), so a team dashboard scanning a month costs one small grouped
aggregate. The single per-issue drill reads agent_runs directly by its issue_id
index — that's a point lookup, not a range scan, so it doesn't need the rollup.
"""

from __future__ import annotations

import datetime
from typing import Any, Callable, Optional


# group_by value -> a zero-arg factory building the ai_usage_hourly column
# expression for that attribution dimension (agent_id/project_id are UUID/
# BIGINT columns cast to text so heterogeneous group keys serialize
# uniformly, matching the legacy raw-SQL `::text` casts). Factories rather
# than pre-built expressions: summarize() calls the factory twice (groups +
# daily queries) and a Label instance must not be reused across two separate
# SELECTs.
def _agent_key():
    from sqlalchemy import String, cast

    from app.models import AiUsageHourly

    return cast(AiUsageHourly.agent_id, String)


def _model_key():
    from app.models import AiUsageHourly

    return AiUsageHourly.model


def _module_key():
    from app.models import AiUsageHourly

    return AiUsageHourly.module


def _project_key():
    from sqlalchemy import String, cast

    from app.models import AiUsageHourly

    return cast(AiUsageHourly.project_id, String)


def _attribution_key():
    from app.models import AiUsageHourly

    return AiUsageHourly.attribution


_GROUP_KEY_FACTORY: dict[str, Callable[[], Any]] = {
    "agent": _agent_key,
    "model": _model_key,
    "module": _module_key,
    "project": _project_key,
    "attribution": _attribution_key,
}

VALID_GROUP_BY = frozenset(_GROUP_KEY_FACTORY)


def _counter_cols():
    """五个计数列的 SUM。每个 SELECT 各调一次——Label 实例不可跨 SELECT 复用，理由
    同本模块顶部的 ``_GROUP_KEY_FACTORY``。"""
    from sqlalchemy import func

    from app.models import AiUsageHourly

    return [
        func.coalesce(func.sum(getattr(AiUsageHourly, name)), 0).label(name)
        for name in (
            "run_count",
            "failed_runs",
            "tool_calls",
            "tool_errors",
            "deliverables",
        )
    ]


def _coerce_bigint(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)


async def summarize(
    *,
    team_id: Any,
    frm: datetime.datetime,
    to: datetime.datetime,
    group_by: str,
) -> dict[str, Any]:
    """Aggregate ai_usage_hourly for one team over [frm, to) grouped by one
    attribution dimension. Returns totals, per-group totals, and a per-day
    series (for the stacked chart)."""
    from sqlalchemy import func, select

    from app.db.session import read_scope
    from app.models import AiUsageHourly

    if group_by not in _GROUP_KEY_FACTORY:
        group_by = "model"

    where_clause = (
        AiUsageHourly.team_id == _coerce_bigint(team_id),
        AiUsageHourly.bucket_hour >= frm,
        AiUsageHourly.bucket_hour < to,
    )

    async with read_scope() as session:
        total = (
            (
                await session.execute(
                    select(
                        func.coalesce(func.sum(AiUsageHourly.prompt_tokens), 0).label(
                            "prompt_tokens"
                        ),
                        func.coalesce(
                            func.sum(AiUsageHourly.completion_tokens), 0
                        ).label("completion_tokens"),
                        func.coalesce(func.sum(AiUsageHourly.total_tokens), 0).label(
                            "total_tokens"
                        ),
                        func.coalesce(
                            func.sum(AiUsageHourly.cached_input_tokens), 0
                        ).label("cached_input_tokens"),
                        func.coalesce(func.sum(AiUsageHourly.cost_cents), 0).label(
                            "cost_cents"
                        ),
                        func.coalesce(func.sum(AiUsageHourly.event_count), 0).label(
                            "event_count"
                        ),
                        *_counter_cols(),
                    ).where(*where_clause)
                )
            )
            .mappings()
            .one()
        )

        # Label instances reused between the SELECT list and the
        # GROUP BY/ORDER BY clauses below so ORDER BY renders the output
        # alias (`ORDER BY cost_cents DESC ...`) rather than repeating the
        # full aggregate expression — matches the legacy raw SQL's
        # alias-based ORDER BY exactly. GROUP BY still expands to the full
        # expression (SQLAlchemy does not alias-reference GROUP BY), which
        # is a different SQL string but an identical grouping.
        grp_label = _GROUP_KEY_FACTORY[group_by]().label("grp")
        cost_label = func.coalesce(func.sum(AiUsageHourly.cost_cents), 0).label(
            "cost_cents"
        )
        total_tokens_label = func.coalesce(
            func.sum(AiUsageHourly.total_tokens), 0
        ).label("total_tokens")
        groups = (
            (
                await session.execute(
                    select(
                        grp_label,
                        func.coalesce(func.sum(AiUsageHourly.prompt_tokens), 0).label(
                            "prompt_tokens"
                        ),
                        func.coalesce(
                            func.sum(AiUsageHourly.completion_tokens), 0
                        ).label("completion_tokens"),
                        total_tokens_label,
                        cost_label,
                        func.coalesce(func.sum(AiUsageHourly.event_count), 0).label(
                            "event_count"
                        ),
                        *_counter_cols(),
                    )
                    .where(*where_clause)
                    .group_by(grp_label)
                    .order_by(cost_label.desc().nulls_last(), total_tokens_label.desc())
                )
            )
            .mappings()
            .all()
        )

        day_label = func.to_char(
            func.date_trunc("day", AiUsageHourly.bucket_hour), "YYYY-MM-DD"
        ).label("day")
        daily_grp_label = _GROUP_KEY_FACTORY[group_by]().label("grp")
        daily = (
            (
                await session.execute(
                    select(
                        day_label,
                        daily_grp_label,
                        func.coalesce(func.sum(AiUsageHourly.total_tokens), 0).label(
                            "total_tokens"
                        ),
                        func.coalesce(func.sum(AiUsageHourly.cost_cents), 0).label(
                            "cost_cents"
                        ),
                        *_counter_cols(),
                    )
                    .where(*where_clause)
                    .group_by(day_label, daily_grp_label)
                    .order_by(day_label.asc())
                )
            )
            .mappings()
            .all()
        )

    return {
        "total": dict(total) if total else {},
        "groups": [dict(g) for g in groups],
        "daily": [dict(d) for d in daily],
    }


async def issue_totals(issue_id: Any, conversation_id: Any = None) -> dict[str, Any]:
    """Per-issue AI spend, summed from agent_runs by the issue's scope keys.

    ``conversation_id`` 是议题的第二条挂靠键（run 经 session 的 conversation 挂上来，
    见 ``issue_scope_keys``）。调用方手里有就传 —— ``usage_router`` 为了鉴权本来就
    读了那一行，``ai_session_id`` 是白拿的。**不传就只按 ``issue_id`` 找**，那样只走
    会话键的 run 不进这个数，下面那句「三处一致」也就只在传了的时候成立。

    两条口径，不能共用一条 WHERE（A2）：

    - ``cost_cents`` / ``prompt_tokens`` / ``completion_tokens`` /
      ``total_tokens`` —— **该议题的全部 run**（root + children）。钱读的是
      ``agent_runs.own_cost_cents``（3d 第 0 票，mig 479）：每行只记自身，不含
      后代，所以全行求和既不双计也不漏掉委派出去的子 run。同一条表达式、**同一组
      OR 键**也用在预算门禁 ``agent_runs_repository.spent_cents_for_issue`` 和驾驶舱的
      ``own_cost_cents_for_issue_runs``（三处共用 ``issue_scope_keys``），必须一致，
      否则同一个议题在 Usage 面与驾驶舱 Budget 格读出两个数。token 列同样不上滚（``add_tokens`` 只累加本
      run 自己那几次调用，子 run 是独立行）。
      ⚠️ **不许改回 ``cost_cents``** —— 那一列是「自身 + 已报到的后代」，只供单行
      展示；按它求和就必须重新加上 root 过滤，而那正是让 Delegate 子 run 的花费整个
      逃出预算的那道口。
    - ``run_count`` —— **只算 root run**。「这个议题跑了几次」问的是顶层运行数，
      不是树上有多少个节点；所以这一列仍带 ``FILTER (WHERE parent_run_id IS
      NULL)``，与钱那一列刻意不同口径。
    """
    from sqlalchemy import func, or_, select

    from app.db.session import read_scope
    from app.models import AgentRuns
    from app.repositories.agent_runs_repository import issue_scope_keys

    root_only = AgentRuns.parent_run_id.is_(None)
    keys = issue_scope_keys(_coerce_bigint(issue_id), _coerce_bigint(conversation_id))
    if not keys:
        # 两个键都没有 → WHERE 会空掉，这条 SUM 就变成整张表的花费（同
        # ``_own_cost_sum_stmt`` 的那道守卫）。一个看着像数的全库总额是最坏的答案。
        raise ValueError("issue_totals needs an issue_id or a conversation_id")

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(
                        func.coalesce(func.sum(AgentRuns.prompt_tokens), 0).label(
                            "prompt_tokens"
                        ),
                        func.coalesce(func.sum(AgentRuns.completion_tokens), 0).label(
                            "completion_tokens"
                        ),
                        func.coalesce(
                            func.sum(
                                AgentRuns.prompt_tokens + AgentRuns.completion_tokens
                            ),
                            0,
                        ).label("total_tokens"),
                        func.coalesce(
                            func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0
                        ).label("cost_cents"),
                        func.count().filter(root_only).label("run_count"),
                    ).where(or_(*keys))
                )
            )
            .mappings()
            .one()
        )
    return (
        dict(row)
        if row
        else {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_cents": 0,
            "run_count": 0,
        }
    )
