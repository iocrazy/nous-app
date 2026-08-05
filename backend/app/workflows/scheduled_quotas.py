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


def _member_quotas_reset_stmt(next_reset: datetime, now: datetime):
    """UPDATE statement for the monthly reset — module-level so tests can
    import + compile the REAL production statement (Phase B5 fix-round
    convention, see tests/test_orm_b5_task1_row_shape_e2e.py)."""
    from sqlalchemy import update

    from app.models import MemberQuotas

    return (
        update(MemberQuotas)
        .where(MemberQuotas.points_used_this_month >= 0)  # match all rows
        .values(points_used_this_month=0, reset_at=next_reset, updated_at=now)
    )


def _grant_daily_free_points_stmt(amount: int, today):
    """SELECT wrapping the ``grant_daily_free_points_batch`` stored function
    (migration 287) as a table-valued expression — ``func.public.<name>(...)
    .table_valued("granted", "skipped")`` compiles to the SAME SELECT text as
    the legacy ``SELECT granted, skipped FROM public.grant_daily_free_points_
    batch(...)`` string, but is built via the SQLAlchemy expression language
    (bind params handled by the ORM layer) instead of a hand-written SQL
    string.

    The SELECT TEXT is equivalent; the CALLING CONVENTION is not — this is
    NOT a behavior-preserving rewrite. The function does real INSERT/UPDATE
    work server-side (idempotent via the daily_point_gifts UNIQUE
    constraint), and the legacy call site ran it via ``db_engine.fetch_one()``
    — a non-committing ``engine.connect()`` — so every one of those writes was
    silently rolled back in production (confirmed via a fresh-container
    reproduction + a live daily_point_gifts read showing no new row since
    2026-06-11). The caller MUST use ``write_scope()`` (explicit
    ``session.begin()``/commit) — that is the fix, not incidental plumbing."""
    from sqlalchemy import func, select

    return select(
        func.public.grant_daily_free_points_batch(amount, today).table_valued(
            "granted", "skipped"
        )
    )


def _reclaim_daily_free_points_stmt(yesterday):
    """SELECT wrapping the ``reclaim_daily_free_points_batch`` stored function
    (migration 287) — same table-valued-function shape as
    ``_grant_daily_free_points_stmt``, and the same calling-convention fix
    applies: the function does real UPDATE/INSERT work server-side, the
    legacy ``db_engine.fetch_one()`` call site silently rolled it back on a
    non-committing connection, and the caller MUST use ``write_scope()`` for
    that work to actually commit — see ``_grant_daily_free_points_stmt``'s
    docstring for the production evidence."""
    from sqlalchemy import func, select

    return select(
        func.public.reclaim_daily_free_points_batch(yesterday).table_valued(
            "reclaimed_count", "total_reclaimed"
        )
    )


@DBOS.step()
async def reset_monthly_quotas_step() -> dict[str, Any]:
    """Zero member_quotas.points_used_this_month + advance reset_at."""
    from app.db.session import write_scope

    # tz-aware UTC so the timestamptz columns aren't bound in the
    # connection's local TZ (asyncpg binds datetimes as timestamptz).
    now = datetime.now(timezone.utc)
    if now.month == 12:
        next_reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_reset = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)

    async with write_scope() as session:
        result = await session.execute(_member_quotas_reset_stmt(next_reset, now))
        count = result.rowcount
    return {"status": "success", "count": count}


@DBOS.step()
async def grant_daily_free_points_step() -> dict[str, Any]:
    """Credit DAILY_FREE_POINTS to each personal team's balance.

    One set-based call (migration 287) — the previous per-team query loop
    was a daily O(users) storm at scale (Tier-3 audit). Idempotent: the
    function claims a daily_point_gifts row per (user_id, gift_date)
    FIRST (UNIQUE constraint), so a re-run can never double-credit."""
    from app.core.config import settings
    from app.db.session import write_scope

    amount = settings.DAILY_FREE_POINTS
    if amount <= 0:
        return {"status": "skipped", "reason": "DAILY_FREE_POINTS <= 0"}

    today = datetime.now(timezone.utc).date()  # DATE column → bind a date object

    # Must be write_scope() (explicit session.begin()/commit), never a bare
    # engine.connect()-backed read — the legacy db_engine.fetch_one() call this
    # replaced ran on a non-committing connection, so the function's internal
    # INSERT/UPDATEs were silently rolled back on every invocation (undetected
    # in production from ~2026-06-11 until this migration).
    async with write_scope() as session:
        row = (
            (await session.execute(_grant_daily_free_points_stmt(amount, today)))
            .mappings()
            .first()
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
    from app.db.session import write_scope

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    # Must be write_scope() — see grant_daily_free_points_step's comment above:
    # the legacy db_engine.fetch_one() call ran on a non-committing connection,
    # silently rolling back this function's INSERT/UPDATEs every run.
    async with write_scope() as session:
        row = (
            (await session.execute(_reclaim_daily_free_points_stmt(yesterday)))
            .mappings()
            .first()
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
