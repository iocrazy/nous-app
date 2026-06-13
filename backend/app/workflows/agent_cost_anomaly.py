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

_FINDINGS_SQL = """
WITH hourly AS (
    SELECT agent_id,
           date_trunc('hour', created_at) AS h,
           SUM(COALESCE(cost_cents, 0))::float AS cost
    FROM public.agent_runs
    WHERE created_at >= now() - interval '7 days'
      AND agent_id IS NOT NULL
    GROUP BY 1, 2
),
base AS (
    SELECT agent_id,
           AVG(cost) AS mean,
           STDDEV_SAMP(cost) AS sd,
           COUNT(*) AS n
    FROM hourly
    WHERE h < date_trunc('hour', now() - interval '1 hour')
    GROUP BY agent_id
),
cur AS (
    SELECT agent_id, cost
    FROM hourly
    WHERE h = date_trunc('hour', now() - interval '1 hour')
)
SELECT c.agent_id::text AS agent_id,
       COALESCE(a.slug, c.agent_id::text) AS agent_slug,
       round(c.cost::numeric, 2)::float AS hour_cost_cents,
       round(b.mean::numeric, 2)::float AS baseline_mean,
       round(b.sd::numeric, 2)::float AS baseline_sd,
       b.n AS baseline_hours,
       round(((c.cost - b.mean) / b.sd)::numeric, 2)::float AS zscore
FROM cur c
JOIN base b USING (agent_id)
LEFT JOIN public.ai_agents a ON a.id = c.agent_id
WHERE b.n >= :min_hours
  AND b.sd > 0
  AND (c.cost - b.mean) / b.sd >= :z
  AND c.cost >= :min_cost
ORDER BY zscore DESC
"""


async def _ensure_anchor_rule() -> int:
    """Get-or-create the inactive system rule alert_history rows hang off."""
    from app.db import engine as db_engine

    rule_id = await db_engine.fetch_val(
        "SELECT id FROM public.alert_rules WHERE name = :name LIMIT 1",
        {"name": _ANCHOR_RULE_NAME},
    )
    if rule_id is not None:
        return int(rule_id)
    created = await db_engine.execute_returning_val(
        "INSERT INTO public.alert_rules "
        "(name, metric_type, condition, threshold, window_minutes, "
        " notification_channel, is_active) "
        "VALUES (:name, 'agent_cost_zscore', 'gte', :z, 60, 'discord', FALSE) "
        "RETURNING id",
        {"name": _ANCHOR_RULE_NAME, "z": Z_THRESHOLD},
    )
    return int(created)


@DBOS.step()
async def detect_agent_cost_anomalies_step() -> dict[str, Any]:
    """One SQL pass; findings → alert_history + WARNING logs."""
    from app.db import engine as db_engine

    findings = await db_engine.fetch_all(
        _FINDINGS_SQL,
        {
            "min_hours": MIN_BASELINE_HOURS,
            "z": Z_THRESHOLD,
            "min_cost": MIN_HOUR_COST_CENTS,
        },
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
        await db_engine.execute(
            "INSERT INTO public.alert_history "
            "(rule_id, rule_name, metric_type, metric_value, threshold, "
            " condition, message, notified) "
            "VALUES (:rid, :rname, 'agent_cost_zscore', :z, :thr, 'gte', "
            ":msg, FALSE)",
            {
                "rid": rule_id,
                "rname": _ANCHOR_RULE_NAME,
                "z": f["zscore"],
                "thr": Z_THRESHOLD,
                "msg": message,
            },
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
