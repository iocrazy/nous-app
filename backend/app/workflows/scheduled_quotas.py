"""Scheduled quotas workflows — port of three points/quota jobs from
`tasks.scheduled_tasks`.

  - reset_monthly_quotas       (1st of month 00:00) — zero out
    member_quotas.points_used_this_month, advance reset_at to next month
  - grant_daily_free_points    (daily 00:00) — credit each personal team
    with DAILY_FREE_POINTS, idempotent via daily_point_gifts unique row
  - reclaim_daily_free_points  (daily 00:20) — claw back unused portion
    of yesterday's gift

These touch real money proxy (points). Per _DEFERRED_TASKS.md, write
integration tests against NAS dev before flipping the celery-beat
schedule off in D3d.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
def reset_monthly_quotas_step() -> dict[str, Any]:
    """Zero member_quotas.points_used_this_month + advance reset_at."""
    from app.db.supabase_client import get_async_supabase_admin

    async def _do() -> int:
        supabase = await get_async_supabase_admin()
        now = datetime.now()
        if now.month == 12:
            next_reset = datetime(now.year + 1, 1, 1)
        else:
            next_reset = datetime(now.year, now.month + 1, 1)

        response = (
            await supabase.table("member_quotas")
            .update(
                {
                    "points_used_this_month": 0,
                    "reset_at": next_reset.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
            .gte("points_used_this_month", 0)  # match all rows
            .execute()
        )
        return len(response.data) if response.data else 0

    count = asyncio.run(_do())
    return {"status": "success", "count": count}


@DBOS.step()
def grant_daily_free_points_step() -> dict[str, Any]:
    """Credit DAILY_FREE_POINTS to each personal team's balance.
    Idempotent: skips if a daily_point_gifts row exists for (user_id,
    today). The maybe_single() / None guard preserves the legacy fix
    for the supabase-py quirk that crashed this task 6x/week."""
    from app.core.config import settings
    from app.db.supabase_client import get_async_supabase_admin
    from app.services.points_service import PointsService

    amount = settings.DAILY_FREE_POINTS
    if amount <= 0:
        return {"status": "skipped", "reason": "DAILY_FREE_POINTS <= 0"}

    async def _do() -> dict[str, int]:
        supabase = await get_async_supabase_admin()
        today = datetime.now().strftime("%Y-%m-%d")

        teams_resp = (
            await supabase.table("teams")
            .select("id, owner_id")
            .eq("is_personal", True)
            .execute()
        )
        personal_teams = teams_resp.data or []

        points_svc = PointsService()
        granted = 0
        skipped = 0

        for team in personal_teams:
            user_id = team["owner_id"]
            team_id = team["id"]

            existing = (
                await supabase.table("daily_point_gifts")
                .select("id")
                .eq("user_id", user_id)
                .eq("gift_date", today)
                .maybe_single()
                .execute()
            )
            if existing is not None and existing.data:
                skipped += 1
                continue

            result = await points_svc.add_points(
                team_id=team_id,
                amount=amount,
                type="daily_gift",
                description=f"Daily free points ({today})",
                user_id=user_id,
            )

            if result.get("success"):
                await supabase.table("daily_point_gifts").insert(
                    {
                        "user_id": user_id,
                        "team_id": team_id,
                        "gift_date": today,
                        "amount_granted": amount,
                        "status": "granted",
                    }
                ).execute()
                granted += 1

        return {"granted": granted, "skipped": skipped}

    result = asyncio.run(_do())
    return {"status": "success", **result}


@DBOS.step()
def reclaim_daily_free_points_step() -> dict[str, Any]:
    """For each of yesterday's granted gifts, sum point_transactions of
    type='consume' since granted_at, reclaim the unused portion."""
    from app.db.supabase_client import get_async_supabase_admin
    from app.services.points_service import PointsService

    async def _do() -> dict[str, int]:
        supabase = await get_async_supabase_admin()
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        gifts_resp = (
            await supabase.table("daily_point_gifts")
            .select("*")
            .eq("gift_date", yesterday)
            .eq("status", "granted")
            .execute()
        )
        gifts = gifts_resp.data or []
        if not gifts:
            return {"reclaimed_count": 0, "total_reclaimed": 0}

        points_svc = PointsService()
        reclaimed_count = 0
        total_reclaimed = 0

        for gift in gifts:
            user_id = gift["user_id"]
            team_id = gift["team_id"]
            amount_granted = gift["amount_granted"]
            granted_at = gift["granted_at"]

            txns_resp = (
                await supabase.table("point_transactions")
                .select("amount")
                .eq("user_id", user_id)
                .eq("team_id", team_id)
                .eq("type", "consume")
                .gte("created_at", granted_at)
                .execute()
            )
            consumed = sum(abs(t["amount"]) for t in (txns_resp.data or []))

            used = min(amount_granted, consumed)
            reclaim_amount = amount_granted - used

            actual_reclaimed = 0
            if reclaim_amount > 0:
                result = await points_svc.reclaim_daily_gift(
                    team_id=team_id,
                    amount=reclaim_amount,
                    user_id=user_id,
                    description=f"Reclaim unused daily gift ({yesterday})",
                )
                actual_reclaimed = result.get("reclaimed", 0)

            await (
                supabase.table("daily_point_gifts")
                .update(
                    {
                        "status": "reclaimed",
                        "amount_consumed": used,
                        "amount_reclaimed": actual_reclaimed,
                        "reclaimed_at": datetime.now().isoformat(),
                    }
                )
                .eq("id", gift["id"])
                .execute()
            )

            reclaimed_count += 1
            total_reclaimed += actual_reclaimed

        return {
            "reclaimed_count": reclaimed_count,
            "total_reclaimed": total_reclaimed,
        }

    result = asyncio.run(_do())
    return {"status": "success", **result}


@DBOS.scheduled("0 0 1 * *")  # 1st of month 00:00 UTC
@DBOS.workflow()
def reset_monthly_quotas_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = reset_monthly_quotas_step()
    logger.info(f"[reset_monthly_quotas] {result}")


@DBOS.scheduled("0 0 * * *")  # daily 00:00 UTC
@DBOS.workflow()
def grant_daily_free_points_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = grant_daily_free_points_step()
    logger.info(f"[grant_daily_free_points] {result}")


@DBOS.scheduled("20 0 * * *")  # daily 00:20 UTC
@DBOS.workflow()
def reclaim_daily_free_points_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = reclaim_daily_free_points_step()
    logger.info(f"[reclaim_daily_free_points] {result}")
