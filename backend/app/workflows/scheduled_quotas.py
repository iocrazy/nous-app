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

    One set-based call (migration 287) — the previous per-team query loop
    was a daily O(users) storm at scale (Tier-3 audit). Idempotent: the
    function claims a daily_point_gifts row per (user_id, gift_date)
    FIRST (UNIQUE constraint), so a re-run can never double-credit."""
    from app.core.config import settings
    from app.db import engine as db_engine

    amount = settings.DAILY_FREE_POINTS
    if amount <= 0:
        return {"status": "skipped", "reason": "DAILY_FREE_POINTS <= 0"}

    today = datetime.now(timezone.utc).date()  # DATE column → bind a date object

    row = await db_engine.fetch_one(
        "SELECT granted, skipped "
        "FROM public.grant_daily_free_points_batch(:amt, :today)",
        {"amt": amount, "today": today},
    )
    if row is None:
        raise RuntimeError("grant_daily_free_points_batch returned no row")
    return {
        "status": "success",
        "granted": row["granted"],
        "skipped": row["skipped"],
    }


@DBOS.step()
async def reclaim_daily_free_points_step() -> dict[str, Any]:
    """Claw back the unused portion of yesterday's gifts.

    One set-based call (migration 287); was 2+ queries per gift. The
    function caps consumed at amount_granted, caps the reclaim at the
    team's current balance, records a negative 'daily_gift_reclaim'
    transaction only when > 0, and flips every processed gift to
    status='reclaimed'."""
    from app.db import engine as db_engine

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    row = await db_engine.fetch_one(
        "SELECT reclaimed_count, total_reclaimed "
        "FROM public.reclaim_daily_free_points_batch(:yesterday)",
        {"yesterday": yesterday},
    )
    if row is None:
        raise RuntimeError("reclaim_daily_free_points_batch returned no row")
    return {
        "status": "success",
        "reclaimed_count": row["reclaimed_count"],
        "total_reclaimed": row["total_reclaimed"],
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
