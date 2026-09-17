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


async def issue_totals(issue_id: Any) -> dict[str, Any]:
    """Per-issue AI spend, summed from agent_runs by the issue_id index.

    两条口径，不能共用一条 WHERE（A2）：

    - ``cost_cents`` / ``run_count`` —— **只算 root run**。子 run 的花费已经滚进
      父行（``run_recorder._finish`` 把 own + children + media 加成树总额），再加
      一遍就是双计。同一条谓词也用在预算门禁
      ``agent_runs_repository.spent_cents_for_issue`` 和
      ``issue_rollup.compute_rollup``（后者经 ``list_for_issue`` 取 root 行），
      三处必须一致，否则同一个议题在 Usage 面与驾驶舱 Budget 格读出两个数。
    - ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` —— **该议题的
      全部 run**。token 列不上滚：``add_tokens`` 只累加本 run 自己那几次调用，
      子 run 是独立行。跟着钱一起按 root 过滤会把子 run 的 token 整个丢掉，而
      前端 ``StatusBlock`` 把 token 与花费渲染在同一行，那就成了「树总额的钱配
      根级的 token」。

    所以 root 谓词写成聚合上的 ``FILTER (WHERE ...)``，不写进 WHERE。
    """
    from sqlalchemy import func, select

    from app.db.session import read_scope
    from app.models import AgentRuns

    root_only = AgentRuns.parent_run_id.is_(None)

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
                            func.sum(AgentRuns.cost_cents).filter(root_only), 0
                        ).label("cost_cents"),
                        func.count().filter(root_only).label("run_count"),
                    ).where(AgentRuns.issue_id == _coerce_bigint(issue_id))
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
