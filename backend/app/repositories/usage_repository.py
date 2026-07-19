"""Read-side queries for the AI Usage panel (W3c).

Range/summary queries read ONLY the ai_usage_hourly rollup cache (never raw
agent_runs), so a team dashboard scanning a month costs one small grouped
aggregate. The single per-issue drill reads agent_runs directly by its issue_id
index — that's a point lookup, not a range scan, so it doesn't need the rollup.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

# group_by value → the ai_usage_hourly column expression (rendered as text so
# heterogeneous keys — uuid agent_id, bigint project_id — serialize uniformly).
_GROUP_KEY_EXPR = {
    "agent": "agent_id::text",
    "model": "model",
    "module": "module",
    "project": "project_id::text",
    "attribution": "attribution",
}

VALID_GROUP_BY = frozenset(_GROUP_KEY_EXPR)


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
    from app.db import engine as db_engine

    if group_by not in _GROUP_KEY_EXPR:
        group_by = "model"
    key_expr = _GROUP_KEY_EXPR[group_by]

    params = {
        "tid": _coerce_bigint(team_id),
        "frm": frm,
        "to": to,
    }
    where = (
        "WHERE team_id = :tid AND bucket_hour >= :frm AND bucket_hour < :to"
    )

    total = await db_engine.fetch_one(
        f"""
        SELECT COALESCE(SUM(prompt_tokens), 0)       AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0)   AS completion_tokens,
               COALESCE(SUM(total_tokens), 0)        AS total_tokens,
               COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
               COALESCE(SUM(cost_cents), 0)          AS cost_cents,
               COALESCE(SUM(event_count), 0)         AS event_count
          FROM public.ai_usage_hourly
          {where}
        """,
        params,
    )

    groups = await db_engine.fetch_all(
        f"""
        SELECT {key_expr}                            AS grp,
               COALESCE(SUM(prompt_tokens), 0)       AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0)   AS completion_tokens,
               COALESCE(SUM(total_tokens), 0)        AS total_tokens,
               COALESCE(SUM(cost_cents), 0)          AS cost_cents,
               COALESCE(SUM(event_count), 0)         AS event_count
          FROM public.ai_usage_hourly
          {where}
          GROUP BY grp
          ORDER BY cost_cents DESC NULLS LAST, total_tokens DESC
        """,
        params,
    )

    daily = await db_engine.fetch_all(
        f"""
        SELECT to_char(date_trunc('day', bucket_hour), 'YYYY-MM-DD') AS day,
               {key_expr}                            AS grp,
               COALESCE(SUM(total_tokens), 0)        AS total_tokens,
               COALESCE(SUM(cost_cents), 0)          AS cost_cents
          FROM public.ai_usage_hourly
          {where}
          GROUP BY day, grp
          ORDER BY day ASC
        """,
        params,
    )

    return {"total": total or {}, "groups": groups, "daily": daily}


async def issue_totals(issue_id: Any) -> dict[str, Any]:
    """Per-issue AI spend, summed from agent_runs by the issue_id index."""
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        """
        SELECT COALESCE(SUM(prompt_tokens), 0)                     AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0)                 AS completion_tokens,
               COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens,
               COALESCE(SUM(cost_cents), 0)                        AS cost_cents,
               COUNT(*)                                            AS run_count
          FROM public.agent_runs
         WHERE issue_id = :iid
        """,
        {"iid": _coerce_bigint(issue_id)},
    )
    return row or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_cents": 0,
        "run_count": 0,
    }
