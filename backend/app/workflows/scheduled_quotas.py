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

from datetime import datetime, timedelta, timezone
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def reset_monthly_quotas_step() -> dict[str, Any]:
    """Zero member_quotas.points_used_this_month + advance reset_at."""
    from app.db import engine as db_engine

    # tz-aware UTC so the timestamptz columns aren't bound in the
    # connection's local TZ (asyncpg binds datetimes as timestamptz).
    now = datetime.now(timezone.utc)
    if now.month == 12:
        next_reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_reset = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)

    count = await db_engine.execute(
        "UPDATE public.member_quotas SET points_used_this_month = 0, "
        "reset_at = :reset_at, updated_at = :now "
        "WHERE points_used_this_month >= 0",  # match all rows
        {"reset_at": next_reset, "now": now},
    )
    return {"status": "success", "count": count}


@DBOS.step()
async def grant_daily_free_points_step() -> dict[str, Any]:
    """Credit DAILY_FREE_POINTS to each personal team's balance.
    Idempotent: skips if a daily_point_gifts row exists for (user_id,
    today), enforced by the UNIQUE(user_id, gift_date) constraint. Until
    migration 223 fixed daily_point_gifts.team_id (uuid → bigint), the
    tracking insert failed daily, so the guard never fired and free points
    were granted every day with no reclaim."""
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.services.billing.points_service import PointsService

    amount = settings.DAILY_FREE_POINTS
    if amount <= 0:
        return {"status": "skipped", "reason": "DAILY_FREE_POINTS <= 0"}

    today = datetime.now(timezone.utc).date()  # DATE column → bind a date object

    personal_teams = await db_engine.fetch_all(
        "SELECT id, owner_id FROM public.teams WHERE is_personal = true"
    )

    points_svc = PointsService()
    granted = 0
    skipped = 0

    for team in personal_teams:
        # owner_id is a uuid (auth.users); team id is a bigint snowflake.
        # str() the uuid so asyncpg's uuid binding + add_points stay happy.
        user_id = str(team["owner_id"])
        team_id = team["id"]

        existing = await db_engine.fetch_one(
            "SELECT id FROM public.daily_point_gifts "
            "WHERE user_id = :uid AND gift_date = :d",
            {"uid": user_id, "d": today},
        )
        if existing:
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
            await db_engine.execute(
                "INSERT INTO public.daily_point_gifts "
                "(user_id, team_id, gift_date, amount_granted, status) "
                "VALUES (:uid, :tid, :d, :amt, 'granted')",
                {"uid": user_id, "tid": team_id, "d": today, "amt": amount},
            )
            granted += 1

    return {"status": "success", "granted": granted, "skipped": skipped}


@DBOS.step()
async def reclaim_daily_free_points_step() -> dict[str, Any]:
    """For each of yesterday's granted gifts, sum point_transactions of
    type='consume' since granted_at, reclaim the unused portion."""
    from app.db import engine as db_engine
    from app.services.billing.points_service import PointsService

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    gifts = await db_engine.fetch_all(
        "SELECT id, user_id, team_id, amount_granted, granted_at "
        "FROM public.daily_point_gifts "
        "WHERE gift_date = :d AND status = 'granted'",
        {"d": yesterday},
    )
    if not gifts:
        return {"status": "success", "reclaimed_count": 0, "total_reclaimed": 0}

    points_svc = PointsService()
    reclaimed_count = 0
    total_reclaimed = 0

    for gift in gifts:
        user_id = str(gift["user_id"])  # uuid → str for asyncpg + add_points
        team_id = gift["team_id"]  # bigint
        amount_granted = gift["amount_granted"]
        granted_at = gift["granted_at"]  # tz-aware datetime

        txns = await db_engine.fetch_all(
            "SELECT amount FROM public.point_transactions "
            "WHERE user_id = :uid AND team_id = :tid AND type = 'consume' "
            "AND created_at >= :since",
            {"uid": user_id, "tid": team_id, "since": granted_at},
        )
        consumed = sum(abs(t["amount"]) for t in txns)

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

        await db_engine.execute(
            "UPDATE public.daily_point_gifts SET status = 'reclaimed', "
            "amount_consumed = :consumed, amount_reclaimed = :reclaimed, "
            "reclaimed_at = :now WHERE id = :gid",
            {
                "consumed": used,
                "reclaimed": actual_reclaimed,
                "now": datetime.now(timezone.utc),
                "gid": gift["id"],
            },
        )

        reclaimed_count += 1
        total_reclaimed += actual_reclaimed

    return {
        "status": "success",
        "reclaimed_count": reclaimed_count,
        "total_reclaimed": total_reclaimed,
    }


@DBOS.scheduled("0 0 1 * *")  # 1st of month 00:00 UTC
@DBOS.workflow()
async def reset_monthly_quotas_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await reset_monthly_quotas_step()
    logger.info(f"[reset_monthly_quotas] {result}")


@DBOS.scheduled("0 0 * * *")  # daily 00:00 UTC
@DBOS.workflow()
async def grant_daily_free_points_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await grant_daily_free_points_step()
    logger.info(f"[grant_daily_free_points] {result}")


@DBOS.scheduled("20 0 * * *")  # daily 00:20 UTC
@DBOS.workflow()
async def reclaim_daily_free_points_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await reclaim_daily_free_points_step()
    logger.info(f"[reclaim_daily_free_points] {result}")
