"""AI usage recording + per-team budget breaker (W3c).

This module owns two responsibilities:

  1. ``record_usage`` — accumulate one finished LLM turn into the
     ``ai_usage_hourly`` rollup cache. The authoritative per-run event is the
     ``agent_runs`` row written by RunRecorder; this function only maintains the
     derived hourly aggregate the Usage panel reads. It is fire-and-forget:
     recording MUST NEVER break the AI call, so every failure is swallowed at
     this boundary with a loud ``logger.error`` (route-C rule: telemetry
     failures don't roll back business work).

  2. The budget breaker — ``is_team_over_budget`` compares a team's
     current-calendar-month spend (summed from ``ai_usage_hourly``) against its
     ``team_ai_budgets`` ceiling. NULL / missing budget = unlimited = False.
     Budget rows are read/written here (config→DB 铁律 — pricing and ceilings
     live in the DB, never client-side).

Attribution is two-level ONLY (``direct_human`` vs ``rule_owner``), derived from
the issue ``origin_kind`` at dispatch — not multica's six-level waterfall.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from loguru import logger

# origin_kind values that mean "an automation fired this on the owner's behalf".
# Everything else (manual, chat_delegate, agent_dispatch, escalation, …) is a
# human action → direct_human.
_RULE_OWNER_ORIGINS = frozenset({"routine", "pipeline"})

_VALID_ATTRIBUTION = frozenset({"direct_human", "rule_owner"})


def attribution_from_origin_kind(origin_kind: Optional[str]) -> str:
    """Map an issue origin_kind to a two-level attribution.

    routine / pipeline → 'rule_owner' (automation on the owner's behalf);
    everything else (and None) → 'direct_human'.
    """
    return "rule_owner" if origin_kind in _RULE_OWNER_ORIGINS else "direct_human"


def _coerce_bigint(value: Any) -> Optional[int]:
    """asyncpg's int8 codec is strict; snowflake ids arriving as str must be
    coerced to int before binding a BIGINT column. None passes through."""
    if value is None:
        return None
    return int(value)


_UPSERT_HOURLY_SQL = """
INSERT INTO public.ai_usage_hourly
    (id, bucket_hour, team_id, project_id, agent_id, model, module, attribution,
     prompt_tokens, completion_tokens, cached_input_tokens, cost_cents,
     event_count, updated_at)
VALUES
    (generate_snowflake_id(), :bucket_hour, :team_id, :project_id,
     CAST(:agent_id AS uuid), :model, :module, :attribution,
     :prompt_tokens, :completion_tokens, :cached_input_tokens, :cost_cents,
     1, now())
ON CONFLICT ON CONSTRAINT ai_usage_hourly_dims_uq DO UPDATE SET
    prompt_tokens = ai_usage_hourly.prompt_tokens + EXCLUDED.prompt_tokens,
    completion_tokens =
        ai_usage_hourly.completion_tokens + EXCLUDED.completion_tokens,
    cached_input_tokens =
        ai_usage_hourly.cached_input_tokens + EXCLUDED.cached_input_tokens,
    cost_cents = ai_usage_hourly.cost_cents + EXCLUDED.cost_cents,
    event_count = ai_usage_hourly.event_count + EXCLUDED.event_count,
    updated_at = now()
"""


async def record_usage(
    *,
    module: str,
    attribution: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    occurred_at: Optional[datetime.datetime] = None,
    team_id: Optional[Any] = None,
    project_id: Optional[Any] = None,
    agent_id: Optional[Any] = None,
    model: Optional[str] = None,
    cached_input_tokens: int = 0,
    cost_cents: Optional[Any] = None,
) -> None:
    """Accumulate one finished LLM turn into the ai_usage_hourly rollup.

    Fire-and-forget: never raises. A recording failure logs ERROR and returns
    so the caller's AI response is unaffected.

    ``cost_cents`` may be None (no pricing configured for the model) — tokens
    are still recorded; the cost accumulator adds 0 in that case, so a team's
    rollup cost is a lower bound whenever some calls were unpriced.
    """
    try:
        occurred = occurred_at or datetime.datetime.now(datetime.timezone.utc)
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=datetime.timezone.utc)
        bucket_hour = occurred.replace(minute=0, second=0, microsecond=0)

        attr = attribution if attribution in _VALID_ATTRIBUTION else "direct_human"
        cost = Decimal(str(cost_cents)) if cost_cents is not None else Decimal(0)

        from app.db import engine as db_engine

        await db_engine.execute(
            _UPSERT_HOURLY_SQL,
            {
                "bucket_hour": bucket_hour,
                "team_id": _coerce_bigint(team_id),
                "project_id": _coerce_bigint(project_id),
                "agent_id": str(agent_id) if agent_id is not None else None,
                "model": model,
                "module": module or "unknown",
                "attribution": attr,
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "cached_input_tokens": int(cached_input_tokens or 0),
                "cost_cents": cost,
            },
        )
    except Exception as exc:  # noqa: BLE001 — boundary swallow, must not break the run
        logger.error(
            "[ai_usage] record_usage failed (non-fatal): "
            f"module={module} team_id={team_id} agent_id={agent_id} err={exc}"
        )


async def get_team_month_spend_cents(team_id: Any) -> Decimal:
    """Sum ai_usage_hourly.cost_cents for the team over the current calendar
    month (server clock). Returns Decimal(0) when there is no spend."""
    from app.db import engine as db_engine

    val = await db_engine.fetch_val(
        """
        SELECT COALESCE(SUM(cost_cents), 0)
          FROM public.ai_usage_hourly
         WHERE team_id = :tid
           AND bucket_hour >= date_trunc('month', now())
        """,
        {"tid": _coerce_bigint(team_id)},
    )
    return Decimal(str(val)) if val is not None else Decimal(0)


async def get_team_budget(team_id: Any) -> Optional[dict[str, Any]]:
    """Return the team_ai_budgets row as a dict, or None if unset."""
    from app.db import engine as db_engine

    return await db_engine.fetch_one(
        """
        SELECT team_id, monthly_budget_cents, updated_by_user_id,
               created_at, updated_at
          FROM public.team_ai_budgets
         WHERE team_id = :tid
        """,
        {"tid": _coerce_bigint(team_id)},
    )


async def upsert_team_budget(
    team_id: Any,
    *,
    monthly_budget_cents: Optional[Any],
    updated_by_user_id: Optional[Any],
) -> Optional[dict[str, Any]]:
    """Insert or update a team's monthly budget. monthly_budget_cents None =
    unlimited. Returns the resulting row."""
    from app.db import engine as db_engine

    budget = (
        Decimal(str(monthly_budget_cents))
        if monthly_budget_cents is not None
        else None
    )
    return await db_engine.execute_returning_one(
        """
        INSERT INTO public.team_ai_budgets
            (team_id, monthly_budget_cents, updated_by_user_id, updated_at)
        VALUES (:tid, :budget, CAST(:uid AS uuid), now())
        ON CONFLICT (team_id) DO UPDATE SET
            monthly_budget_cents = EXCLUDED.monthly_budget_cents,
            updated_by_user_id = EXCLUDED.updated_by_user_id,
            updated_at = now()
        RETURNING team_id, monthly_budget_cents, updated_by_user_id,
                  created_at, updated_at
        """,
        {
            "tid": _coerce_bigint(team_id),
            "budget": budget,
            "uid": str(updated_by_user_id) if updated_by_user_id is not None else None,
        },
    )


async def is_team_over_budget(team_id: Optional[Any]) -> bool:
    """True when the team's current calendar-month AI spend meets or exceeds its
    configured monthly ceiling. No budget row, NULL ceiling, or no resolvable
    team → False (unlimited). Never raises — a lookup failure returns False so a
    telemetry hiccup can't wedge the whole autopilot."""
    if team_id is None:
        return False
    try:
        budget_row = await get_team_budget(team_id)
        if not budget_row:
            return False
        ceiling = budget_row.get("monthly_budget_cents")
        if ceiling is None:
            return False
        spend = await get_team_month_spend_cents(team_id)
        return spend >= Decimal(str(ceiling))
    except Exception as exc:  # noqa: BLE001 — fail-open so telemetry can't wedge dispatch
        logger.error(f"[ai_usage] is_team_over_budget failed (fail-open): {exc}")
        return False


# Re-export UUID for callers/tests that construct agent_id values.
__all__ = [
    "attribution_from_origin_kind",
    "record_usage",
    "get_team_month_spend_cents",
    "get_team_budget",
    "upsert_team_budget",
    "is_team_over_budget",
    "UUID",
]
