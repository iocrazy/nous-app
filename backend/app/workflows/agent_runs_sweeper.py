"""agent_runs_sweeper — DBOS @scheduled port of the legacy
`tasks.agent_runs_sweeper.sweep` Celery beat job.

The legacy task ran every 60s under celery-beat, fenced by a Postgres
advisory lock so multiple beat workers wouldn't double-execute. DBOS
handles that natively: the scheduler generates a deterministic
workflow_id (`sched-<name>-<iso>`) per cron tick, and the system DB
rejects duplicate workflow_ids — so only one DBOS worker actually
runs each tick. The advisory_lock indirection is dropped.

Two jobs (unchanged from the Celery version):
  1. mark zombie `status='running'` rows (heartbeat_at < now-2min)
     as `heartbeat_lost`
  2. recompute monthly token/cost spend per agent → flip
     ai_agents.paused_reason='budget' on overrun, clear it on
     undershoot (without clobbering manual pauses)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from dbos import DBOS
from loguru import logger

HEARTBEAT_STALENESS_SECONDS = 120


@DBOS.step()
def mark_heartbeat_lost_step() -> int:
    """Flip running rows whose heartbeat is older than 2 minutes."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    async def _do() -> int:
        runs_repo = get_agent_runs_repository()
        stale_before = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(
            seconds=HEARTBEAT_STALENESS_SECONDS
        )
        return await runs_repo.mark_heartbeat_lost(stale_before=stale_before)

    return asyncio.run(_do())


@DBOS.step()
def recompute_monthly_budgets_step() -> int:
    """Sum this month's spend per agent, flip paused_reason='budget' on
    overrun. Returns count of agents whose paused_reason transitioned."""
    from app.db.supabase_client import get_async_supabase_admin
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    async def _do() -> int:
        client = await get_async_supabase_admin()
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        runs_repo = get_agent_runs_repository()
        rows = await runs_repo.monthly_usage_by_agent(
            month_start=month_start,
            month_end=now.replace(microsecond=0),
        )

        totals: dict[str, dict[str, float]] = {}
        for r in rows:
            aid = r["agent_id"]
            bucket = totals.setdefault(aid, {"tokens": 0, "cost_cents": 0.0})
            bucket["tokens"] += int(r.get("total_tokens") or 0)
            if r.get("cost_cents") is not None:
                bucket["cost_cents"] += float(r["cost_cents"])

        if not totals:
            return 0

        agents_result = (
            await client.table("ai_agents")
            .select("id,monthly_token_budget,monthly_cost_cents_budget,paused_reason")
            .in_("id", list(totals.keys()))
            .execute()
        )

        transitions = 0
        for agent in agents_result.data or []:
            aid = agent["id"]
            t = totals.get(aid, {"tokens": 0, "cost_cents": 0.0})
            token_budget = agent.get("monthly_token_budget")
            cost_budget = agent.get("monthly_cost_cents_budget")
            paused_reason = agent.get("paused_reason")

            over_tokens = token_budget is not None and t["tokens"] > int(token_budget)
            over_cost = cost_budget is not None and t["cost_cents"] > float(cost_budget)
            should_pause = over_tokens or over_cost

            if should_pause and paused_reason != "budget":
                # Don't clobber a manual pause.
                if paused_reason is None:
                    await (
                        client.table("ai_agents")
                        .update({"paused_reason": "budget"})
                        .eq("id", aid)
                        .execute()
                    )
                    transitions += 1
                    logger.info(
                        f"[sweeper] agent {aid} paused_by_budget "
                        f"(tokens={t['tokens']}, cost={t['cost_cents']})"
                    )
            elif not should_pause and paused_reason == "budget":
                await (
                    client.table("ai_agents")
                    .update({"paused_reason": None})
                    .eq("id", aid)
                    .execute()
                )
                transitions += 1
                logger.info(f"[sweeper] agent {aid} unpaused (budget cleared)")

        return transitions

    return asyncio.run(_do())


@DBOS.scheduled("* * * * *")  # every minute
@DBOS.workflow()
def agent_runs_sweeper_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Run a single sweeper tick. DBOS dedup via workflow_id =
    `sched-agent_runs_sweeper_workflow-<iso>` ensures only one worker
    fires per cron tick across the cluster."""
    heartbeat_lost = mark_heartbeat_lost_step()
    transitions = recompute_monthly_budgets_step()
    if heartbeat_lost or transitions:
        logger.info(
            f"[sweeper] heartbeat_lost={heartbeat_lost} "
            f"budget_transitions={transitions}"
        )
