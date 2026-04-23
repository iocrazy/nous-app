"""Agent runs sweeper — runs every 60s via Celery beat.

Two jobs:
  1. Mark ``status='running'`` rows with heartbeat_at < now-2min as
     ``heartbeat_lost``. Prevents zombie rows when a process dies.
  2. Recompute monthly token/cost usage per agent, compare against
     ai_agents.monthly_*_budget, flip ai_agents.paused_reason='budget'
     when exceeded. Runner's pre-flight RunRecorder.start() rejects
     runs on paused agents.

Guarded by a Postgres advisory lock via the migration-149 wrappers
``public.try_advisory_lock`` / ``public.advisory_unlock``, which
delegate to pg_try_advisory_lock / pg_advisory_unlock. We can't call
the pg_* built-ins directly through PostgREST RPC — it only resolves
functions declared in the public schema with matching named
parameters. The wrappers exist exactly to bridge that so multiple
beat workers never step on each other. Lock is released at task
end; if the task crashes mid-way, Postgres releases the lock when
the session ends.

This module registers the Celery task and the beat schedule entry
is wired in ``backend/app/celery_app.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.celery_app import celery_app
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_runs_repository import AgentRunsRepository

HEARTBEAT_STALENESS_SECONDS = (
    120  # 2 minutes — matches RunRecorder's 15s write cadence * 8
)
# Postgres advisory lock key: hashtext('agent_runs_sweeper'). We pick a
# fixed int here to avoid depending on pg_catalog.hashtext at Python level.
# This constant must stay stable across deployments; change it and you
# invalidate any existing lock holders gracefully (old holders unlock,
# new key takes over).
SWEEPER_LOCK_KEY = 93_827_412_001


async def _acquire_lock(client: Any) -> bool:
    """Attempt the advisory lock via the migration-149 wrapper.

    Returns True iff we got the lock. The wrapper's named ``lock_key``
    parameter lines up with what supabase-py sends as JSON body so
    PostgREST can route the call.
    """
    try:
        result = await client.rpc(
            "try_advisory_lock", {"lock_key": SWEEPER_LOCK_KEY}
        ).execute()
        locked = bool(result.data) if result.data is not None else False
        return locked
    except Exception as err:
        logger.warning(f"[sweeper] advisory_lock failed: {err}")
        return False


async def _release_lock(client: Any) -> None:
    try:
        await client.rpc("advisory_unlock", {"lock_key": SWEEPER_LOCK_KEY}).execute()
    except Exception as err:
        logger.warning(f"[sweeper] advisory_unlock failed: {err}")


async def _recompute_monthly_budgets(client: Any) -> int:
    """Aggregate this calendar month's spend per agent, set/clear paused_reason.

    Returns the count of agents whose paused_reason transitioned this tick.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    runs_repo = AgentRunsRepository()
    rows = await runs_repo.monthly_usage_by_agent(
        month_start=month_start,
        month_end=now.replace(microsecond=0),
    )

    # Sum per agent.
    totals: dict[str, dict[str, float]] = {}
    for r in rows:
        aid = r["agent_id"]
        bucket = totals.setdefault(aid, {"tokens": 0, "cost_cents": 0.0})
        bucket["tokens"] += int(r.get("total_tokens") or 0)
        if r.get("cost_cents") is not None:
            bucket["cost_cents"] += float(r["cost_cents"])

    if not totals:
        return 0

    # Load all agents with budgets set.
    agents_result = (
        await client.table("ai_agents")
        .select("id,monthly_token_budget,monthly_cost_cents_budget,paused_reason")
        .in_("id", list(totals.keys()))
        .execute()
    )
    transitions = 0
    for agent in agents_result.data or []:
        aid = agent["id"]
        totals_row = totals.get(aid, {"tokens": 0, "cost_cents": 0.0})
        token_budget = agent.get("monthly_token_budget")
        cost_budget = agent.get("monthly_cost_cents_budget")
        paused_reason = agent.get("paused_reason")

        over_tokens = token_budget is not None and totals_row["tokens"] > int(
            token_budget
        )
        over_cost = cost_budget is not None and totals_row["cost_cents"] > float(
            cost_budget
        )
        should_be_paused_by_budget = over_tokens or over_cost

        if should_be_paused_by_budget and paused_reason != "budget":
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
                    f"(tokens={totals_row['tokens']}, cost={totals_row['cost_cents']})"
                )
        elif not should_be_paused_by_budget and paused_reason == "budget":
            # Budget cleared (new month / higher budget) — unpause.
            await (
                client.table("ai_agents")
                .update({"paused_reason": None})
                .eq("id", aid)
                .execute()
            )
            transitions += 1
            logger.info(f"[sweeper] agent {aid} unpaused (budget cleared)")

    return transitions


async def _sweep_async() -> dict[str, int]:
    """One sweeper tick. Returns stats dict for observability."""
    client = await get_async_supabase_admin()

    if not await _acquire_lock(client):
        logger.debug("[sweeper] advisory lock held by another worker — skipping tick")
        return {"skipped": 1, "heartbeat_lost": 0, "budget_transitions": 0}

    try:
        runs_repo = AgentRunsRepository()
        stale_before = (
            datetime.now(timezone.utc).replace(microsecond=0) - _stale_delta()
        )
        heartbeat_lost = await runs_repo.mark_heartbeat_lost(stale_before=stale_before)
        transitions = await _recompute_monthly_budgets(client)
        if heartbeat_lost or transitions:
            logger.info(
                f"[sweeper] heartbeat_lost={heartbeat_lost} "
                f"budget_transitions={transitions}"
            )
        return {
            "skipped": 0,
            "heartbeat_lost": heartbeat_lost,
            "budget_transitions": transitions,
        }
    finally:
        await _release_lock(client)


def _stale_delta():
    from datetime import timedelta

    return timedelta(seconds=HEARTBEAT_STALENESS_SECONDS)


@celery_app.task(name="app.tasks.agent_runs_sweeper.sweep")
def sweep() -> dict[str, int]:
    """Celery task wrapper. Runs _sweep_async on a fresh event loop."""
    return asyncio.run(_sweep_async())
