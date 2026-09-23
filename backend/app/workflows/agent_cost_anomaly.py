"""Agent cost anomaly detector (Phase 4.5 — canvas plan §4.5 Week 3).

Hourly z-score over per-agent hourly spend. No new tables (the plan
explicitly rejected a ``cost_anomalies`` table): findings land in the
existing ``alert_history`` (admin Alerts page) anchored to a system
rule row, plus a WARNING in application_logs.

Method: for each agent, the trailing 7-day distribution of HOURLY
own_cost_cents (agent_runs — each row's OWN spend, descendants excluded)
is the baseline; the last CLOSED hour is the sample. Flag when all of:
    baseline has >= MIN_BASELINE_HOURS hours with spend,
    z = (hour_cost - mean) / stddev >= Z_THRESHOLD,
    hour_cost >= the absolute floor (tiny baselines make huge z-scores
    out of pocket change). The floor is admin-tunable: each tick reads
    ``system_settings['agent_cost_anomaly.min_hour_cost_cents']`` and falls
    back to DEFAULT_MIN_HOUR_COST_CENTS when the key is missing or invalid.

The anchor rule is ensured idempotently with ``is_active = FALSE`` so
the admin's manual /check loop (which only walks active rules) never
double-evaluates it.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from dbos import DBOS
from loguru import logger

Z_THRESHOLD = 3.0
MIN_BASELINE_HOURS = 24
# Absolute floor (cents) on the flagged hour's own spend. Derivation: under the
# own-spend semantics (mig 479 — the CTE sums own_cost_cents), production on
# 2026-09-22 had 38 agent-hours with spend in the last 30 days: p50 0.15¢ /
# p75 0.46¢ / p90 1.8¢ / max 30¢. The old value 50 was set under the folded
# cost_cents semantics (descendants rolled into the parent row) and under
# neither semantics did any hour ever clear it — the detector could not fire.
# 0.5¢ sits just above p75. Admins change it at runtime via
# system_settings['agent_cost_anomaly.min_hour_cost_cents'] (admin Settings
# GET/PUT /api/v1/admin/settings/agent-cost-anomaly); this is only the fallback.
DEFAULT_MIN_HOUR_COST_CENTS = 0.5
MIN_HOUR_COST_SETTING_KEY = "agent_cost_anomaly.min_hour_cost_cents"

_ANCHOR_RULE_NAME = "Agent cost anomaly (system)"


def _findings_stmt(min_hours: int, z: float, min_cost: float):
    """Core CTE expression for the hourly z-score scan (ORM, Phase B4).

    Mirrors the legacy ``_FINDINGS_SQL`` CTE-by-CTE:
      hourly — per-(agent, hour) spend over the trailing 7 days. Spend is
               ``own_cost_cents`` (this run's OWN spend): the old
               ``cost_cents`` folds descendants into the parent row, so one
               delegating run inflated its parent agent's hour twice over and
               the alert named the wrong agent.
      base   — per-agent mean/stddev/n over every hour EXCEPT the last
               closed one (the baseline).
      cur    — the last closed hour's spend per agent (the sample).

    ``mean``/``sd`` are explicitly cast to FLOAT in the ``base`` CTE so the
    zscore division stays float/float arithmetic (matching Postgres' native
    ``avg(double precision)``/``stddev_samp(double precision)`` — without the
    explicit cast SQLAlchemy's Numeric-comparator machinery silently upgrades
    the divisor to NUMERIC, which would subtly change the threshold
    comparison's precision, not just the rounded display value).
    """
    from sqlalchemy import Float, Numeric, Text, bindparam, cast, func, select, text

    from app.models import AgentRuns, AiAgents

    hour_trunc = func.date_trunc("hour", AgentRuns.created_at)
    hourly = (
        select(
            AgentRuns.agent_id.label("agent_id"),
            hour_trunc.label("h"),
            cast(func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), Float).label(
                "cost"
            ),
        )
        .where(
            AgentRuns.created_at >= func.now() - text("interval '7 days'"),
            AgentRuns.agent_id.isnot(None),
        )
        .group_by(AgentRuns.agent_id, hour_trunc)
        .cte("hourly")
    )

    cur_hour_expr = func.date_trunc("hour", func.now() - text("interval '1 hour'"))

    base = (
        select(
            hourly.c.agent_id.label("agent_id"),
            cast(func.avg(hourly.c.cost), Float).label("mean"),
            cast(func.stddev_samp(hourly.c.cost), Float).label("sd"),
            func.count().label("n"),
        )
        .where(hourly.c.h < cur_hour_expr)
        .group_by(hourly.c.agent_id)
        .cte("base")
    )
    cur = (
        select(hourly.c.agent_id.label("agent_id"), hourly.c.cost.label("cost"))
        .where(hourly.c.h == cur_hour_expr)
        .cte("cur")
    )

    zscore_expr = (cur.c.cost - base.c.mean) / base.c.sd

    return (
        select(
            cast(cur.c.agent_id, Text).label("agent_id"),
            func.coalesce(AiAgents.slug, cast(cur.c.agent_id, Text)).label(
                "agent_slug"
            ),
            cast(func.round(cast(cur.c.cost, Numeric), 2), Float).label(
                "hour_cost_cents"
            ),
            cast(func.round(cast(base.c.mean, Numeric), 2), Float).label(
                "baseline_mean"
            ),
            cast(func.round(cast(base.c.sd, Numeric), 2), Float).label("baseline_sd"),
            base.c.n.label("baseline_hours"),
            cast(func.round(cast(zscore_expr, Numeric), 2), Float).label("zscore"),
        )
        .select_from(cur.join(base, cur.c.agent_id == base.c.agent_id))
        .outerjoin(AiAgents, AiAgents.id == cur.c.agent_id)
        .where(
            base.c.n >= bindparam("min_hours", min_hours),
            base.c.sd > 0,
            zscore_expr >= bindparam("z", z),
            cur.c.cost >= bindparam("min_cost", min_cost),
        )
        .order_by(zscore_expr.desc())
    )


async def _ensure_anchor_rule() -> int:
    """Get-or-create the inactive system rule alert_history rows hang off."""
    from app.services.alerting.anchor_rule import ensure_anchor_rule

    return await ensure_anchor_rule(
        name=_ANCHOR_RULE_NAME,
        metric_type="agent_cost_zscore",
        threshold=Z_THRESHOLD,
        is_active=False,
    )


def parse_min_hour_cost_cents(raw: Any) -> float | None:
    """Parse a stored floor value; ``None`` when absent/blank/invalid.

    ``system_settings.value`` is JSONB — may arrive as a JSON number, a decoded
    str, or raw JSON text (``'"2"'``); strip surrounding quotes defensively
    (same handling as ``memory.registry._read_provider_setting``). Booleans,
    negatives and non-finite values are invalid.
    """
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().strip('"').strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


async def read_min_hour_cost_cents() -> float:
    """Current floor: the admin setting, else DEFAULT_MIN_HOUR_COST_CENTS.

    Missing key → default silently (the normal unconfigured state). A present
    but unparsable/negative value, or a failed read → default + WARNING, so a
    bad admin write is visible in application_logs instead of silently
    changing alert sensitivity.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import SystemSettings

    try:
        async with read_scope() as session:
            raw = (
                await session.execute(
                    select(SystemSettings.value).where(
                        SystemSettings.key == MIN_HOUR_COST_SETTING_KEY
                    )
                )
            ).scalar()
    except Exception as e:  # noqa: BLE001 — fall back, but say so
        logger.warning(
            f"[agent_cost_anomaly] reading {MIN_HOUR_COST_SETTING_KEY} failed "
            f"({e!r}); using default {DEFAULT_MIN_HOUR_COST_CENTS}"
        )
        return DEFAULT_MIN_HOUR_COST_CENTS
    if raw is None:
        return DEFAULT_MIN_HOUR_COST_CENTS
    parsed = parse_min_hour_cost_cents(raw)
    if parsed is None:
        logger.warning(
            f"[agent_cost_anomaly] invalid {MIN_HOUR_COST_SETTING_KEY}={raw!r}; "
            f"using default {DEFAULT_MIN_HOUR_COST_CENTS}"
        )
        return DEFAULT_MIN_HOUR_COST_CENTS
    return parsed


@DBOS.step()
async def detect_agent_cost_anomalies_step() -> dict[str, Any]:
    """One SQL pass; findings → alert_history + WARNING logs."""
    from sqlalchemy import insert

    from app.db.session import read_scope, write_scope
    from app.models import AlertHistory

    min_cost = await read_min_hour_cost_cents()
    async with read_scope() as session:
        findings = (
            (
                await session.execute(
                    _findings_stmt(MIN_BASELINE_HOURS, Z_THRESHOLD, min_cost)
                )
            )
            .mappings()
            .all()
        )
    if not findings:
        return {"status": "success", "anomalies": 0}

    rule_id = await _ensure_anchor_rule()
    for f in findings:
        message = (
            f"Agent '{f['agent_slug']}' spent {f['hour_cost_cents']:.2f}¢ "
            f"last hour — z={f['zscore']:.1f} vs its 7d hourly baseline "
            f"(mean {f['baseline_mean']:.2f}¢, sd {f['baseline_sd']:.2f}¢, "
            f"n={f['baseline_hours']}h)."
        )
        logger.warning(f"[agent_cost_anomaly] {message}")
        async with write_scope() as session:
            await session.execute(
                insert(AlertHistory).values(
                    rule_id=rule_id,
                    rule_name=_ANCHOR_RULE_NAME,
                    metric_type="agent_cost_zscore",
                    metric_value=f["zscore"],
                    threshold=Z_THRESHOLD,
                    condition="gte",
                    message=message,
                    notified=False,
                )
            )
    return {"status": "success", "anomalies": len(findings)}


@DBOS.scheduled("7 * * * *")  # hourly at :07 — the previous hour is closed
@DBOS.workflow()
async def agent_cost_anomaly_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await detect_agent_cost_anomalies_step()
    if result.get("anomalies"):
        logger.warning(f"[agent_cost_anomaly] {result}")
    else:
        logger.info(f"[agent_cost_anomaly] {result}")
