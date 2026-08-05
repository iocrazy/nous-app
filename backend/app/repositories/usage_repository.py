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
    """Per-issue AI spend, summed from agent_runs by the issue_id index."""
    from sqlalchemy import func, select

    from app.db.session import read_scope
    from app.models import AgentRuns

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
                        func.coalesce(func.sum(AgentRuns.cost_cents), 0).label(
                            "cost_cents"
                        ),
                        func.count().label("run_count"),
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
