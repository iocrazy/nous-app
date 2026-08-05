"""Agent cost anomaly detector (Phase 4.5 — canvas plan §4.5 Week 3).

Hourly z-score over per-agent hourly spend. No new tables (the plan
explicitly rejected a ``cost_anomalies`` table): findings land in the
existing ``alert_history`` (admin Alerts page) anchored to a system
rule row, plus a WARNING in application_logs.

Method: for each agent, the trailing 7-day distribution of HOURLY
cost_cents (agent_runs) is the baseline; the last CLOSED hour is the
sample. Flag when all of:
    baseline has >= MIN_BASELINE_HOURS hours with spend,
    z = (hour_cost - mean) / stddev >= Z_THRESHOLD,
    hour_cost >= MIN_HOUR_COST_CENTS (absolute floor — tiny baselines
    make huge z-scores out of pocket change).

The anchor rule is ensured idempotently with ``is_active = FALSE`` so
the admin's manual /check loop (which only walks active rules) never
double-evaluates it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dbos import DBOS
from loguru import logger

Z_THRESHOLD = 3.0
MIN_BASELINE_HOURS = 24
MIN_HOUR_COST_CENTS = 50.0

_ANCHOR_RULE_NAME = "Agent cost anomaly (system)"


def _findings_stmt(min_hours: int, z: float, min_cost: float):
    """Core CTE expression for the hourly z-score scan (ORM, Phase B4).

    Mirrors the legacy ``_FINDINGS_SQL`` CTE-by-CTE:
      hourly — per-(agent, hour) spend over the trailing 7 days.
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
            cast(func.sum(func.coalesce(AgentRuns.cost_cents, 0)), Float).label("cost"),
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
    from sqlalchemy import insert, select

    from app.db.session import read_scope, write_scope
    from app.models import AlertRules

    async with read_scope() as session:
        rule_id = (
            await session.execute(
                select(AlertRules.id)
                .where(AlertRules.name == _ANCHOR_RULE_NAME)
                .limit(1)
            )
        ).scalar()
    if rule_id is not None:
        return int(rule_id)

    async with write_scope() as session:
        created = (
            await session.execute(
                insert(AlertRules)
                .values(
                    name=_ANCHOR_RULE_NAME,
                    metric_type="agent_cost_zscore",
                    condition="gte",
                    threshold=Z_THRESHOLD,
                    window_minutes=60,
                    notification_channel="discord",
                    is_active=False,
                )
                .returning(AlertRules.id)
            )
        ).scalar()
    return int(created)


@DBOS.step()
async def detect_agent_cost_anomalies_step() -> dict[str, Any]:
    """One SQL pass; findings → alert_history + WARNING logs."""
    from sqlalchemy import insert

    from app.db.session import read_scope, write_scope
    from app.models import AlertHistory

    async with read_scope() as session:
        findings = (
            (
                await session.execute(
                    _findings_stmt(MIN_BASELINE_HOURS, Z_THRESHOLD, MIN_HOUR_COST_CENTS)
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
            f"Agent '{f['agent_slug']}' spent {f['hour_cost_cents']:.0f}¢ "
            f"last hour — z={f['zscore']:.1f} vs its 7d hourly baseline "
            f"(mean {f['baseline_mean']:.0f}¢, sd {f['baseline_sd']:.0f}¢, "
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
