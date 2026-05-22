"""scheduled_master — master scheduler that fires user_schedules rows.

Runs every minute via @DBOS.scheduled. Reads `public.user_schedules`
where enabled=true AND next_fire_at <= now(); for each due row,
dispatches the corresponding task_type workflow and recomputes
next_fire_at via croniter.

Why a single master vs N decorators
-----------------------------------
* Hard-coded `@DBOS.scheduled("0 9 * * *")` requires a code change +
  deploy to add a new schedule. Users (and ops) want to add/edit/
  disable schedules at runtime.
* Each per-user "daily summary email" schedule would need its own
  decorator → schedule explosion as user count grows.
* Centralizing dispatch lets us reuse one set of safety guards
  (timer 三防护 borrowed from openclaw / lane queue routing) instead
  of replicating across N decorators.

Timer safety guards (borrowed from openclaw cron/service/timer.ts:780)
----------------------------------------------------------------------
* MIN_REFIRE_GAP_MS=100 — even if next_fire_at races to now-now (rare,
  e.g. user sets cron "* * * * *" with 0 missed minutes), enforce a
  100ms floor so we don't tight-loop on a misconfigured row.
* MAX_TIMER_DELAY_MS=60_000 — DBOS @scheduled cron is already 1-min
  precision, but we re-check the table on every tick (vs. waiting for
  the next cron event) so a freshly-INSERTed schedule with
  next_fire_at <= now() fires within ≤1 min instead of waiting for
  the row's specific cron tick.
* armRunningRecheckTimer — handled implicitly by DBOS @scheduled
  (each tick is a separate workflow invocation; long ones don't
  block subsequent ticks because DBOS uses workflow_id dedup).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from dbos import DBOS
from loguru import logger

_MIN_REFIRE_GAP_MS = 100
_BATCH_SIZE = 100  # don't dispatch more than this per tick


@DBOS.step()
async def fire_due_schedules_step() -> Dict[str, int]:
    """Scan user_schedules for due rows; dispatch each; advance
    next_fire_at via croniter. Returns counters for telemetry."""
    # Direct PG (asyncpg) — supabase-py's PostgREST/httpx path leaked a
    # CLOSE_WAIT connection per call (Issue #199 Bug C / 2026-05-22 incident).
    from app.db.pg_pool import get_pool

    now = datetime.now(timezone.utc)

    # Pull due rows. enabled=true + next_fire_at <= now.
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            records = await conn.fetch(
                "SELECT * FROM public.user_schedules "
                "WHERE enabled = true AND next_fire_at <= $1 "
                "ORDER BY next_fire_at LIMIT $2",
                now,
                _BATCH_SIZE,
            )
        rows = [dict(r) for r in records]
    except Exception as exc:
        logger.opt(exception=True).warning(f"[scheduled_master] fetch failed: {exc}")
        return {"due": 0, "fired": 0, "errors": 1}

    if not rows:
        return {"due": 0, "fired": 0, "errors": 0}

    fired = 0
    errors = 0
    for row in rows:
        try:
            await _dispatch_one(row)
            fired += 1
        except Exception as exc:
            errors += 1
            logger.opt(exception=True).warning(
                f"[scheduled_master] dispatch row {row.get('id')} failed: {exc}"
            )
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE public.user_schedules SET fail_count = $1, "
                        "last_error = $2 WHERE id = $3",
                        (row.get("fail_count") or 0) + 1,
                        str(exc)[:500],
                        row["id"],
                    )
            except Exception:
                pass

    return {"due": len(rows), "fired": fired, "errors": errors}


async def _dispatch_one(row: Dict[str, Any]) -> None:
    """Dispatch a single due row + advance its next_fire_at."""
    task_type = row.get("task_type") or ""
    payload = row.get("payload") or {}
    user_id = row.get("user_id")
    sched_id = row["id"]

    # Compute next fire time before dispatch so a slow dispatch doesn't
    # delay the next tick.
    next_at = _compute_next_fire(row.get("cron_expr") or "* * * * *")

    # Update the row first (advance next_fire_at + bump counters) so
    # concurrent master ticks don't double-fire the same schedule.
    # NOTE: this is best-effort serialization — for true cluster-wide
    # exactly-once we'd need an advisory lock or DBOS workflow_id
    # dedup keyed on (id, last_fired_at). Acceptable here because
    # task_type dispatchers are themselves idempotent (PR #154 ensures
    # DBOS workflow_id dedup) and double-firing means at most one
    # extra task that the dispatcher will short-circuit.
    from app.db.pg_pool import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.user_schedules SET last_fired_at = $1, "
            "next_fire_at = $2, fire_count = $3, last_error = NULL "
            "WHERE id = $4",
            datetime.now(timezone.utc),
            next_at,
            (row.get("fire_count") or 0) + 1,
            sched_id,
        )

    # Dispatch via the task_type → workflow registry. For now we route
    # through start_workflow_routed so the existing routing table
    # decides which workflow callable to fire. Unknown task_type just
    # logs + bumps fail_count (next call up the stack).
    from app.services.infra.dbos_orchestrator import start_workflow_routed

    # The mapping task_type → workflow callable lives in
    # app/workflows/__init__.py + dispatch routing. For each task_type
    # the master scheduler supports we'd add a small import + dispatch
    # entry here. Initial supported types: parse / download / ai_summary
    # — extend as user UX surfaces more.
    workflow_callable = await _resolve_workflow_callable(task_type)
    if workflow_callable is None:
        logger.warning(
            f"[scheduled_master] unknown task_type={task_type} for "
            f"schedule {sched_id} — skipping"
        )
        return

    kwargs = dict(payload)
    if user_id:
        kwargs.setdefault("user_id", str(user_id))

    await start_workflow_routed(
        task_type,
        dbos_workflow_callable=workflow_callable,
        dbos_workflow_kwargs=kwargs,
    )
    logger.info(
        f"[scheduled_master] fired schedule={sched_id} task_type={task_type} "
        f"user={user_id} next_at={next_at.isoformat()}"
    )


async def _resolve_workflow_callable(task_type: str):
    """Map task_type string to the workflow callable. Kept here as a
    small static dispatch table — extends as we expose new types in the
    user-facing schedule UI. Unknown types return None (caller logs)."""
    if task_type == "parse":
        from app.workflows.parse import parse_workflow

        return parse_workflow
    if task_type == "download":
        from app.workflows.download import download_workflow

        return download_workflow
    if task_type == "ai_summary":
        from app.workflows.ai_summary import ai_summary_workflow

        return ai_summary_workflow
    if task_type == "transcode":
        from app.workflows.transcode import transcode_workflow

        return transcode_workflow
    return None


def _compute_next_fire(cron_expr: str) -> datetime:
    """Return the next fire time after now() for the given 5-field cron.
    Falls back to now+1h if the cron is invalid (operator misconfig).
    """
    try:
        from croniter import croniter

        base = datetime.now(timezone.utc)
        itr = croniter(cron_expr, base)
        next_at = itr.get_next(datetime)
        # MIN_REFIRE_GAP_MS guard — even if cron fires immediately, push
        # at least 100ms so we don't tight-loop.
        if (next_at - base).total_seconds() * 1000 < _MIN_REFIRE_GAP_MS:
            from datetime import timedelta

            next_at = base + timedelta(milliseconds=_MIN_REFIRE_GAP_MS)
        return next_at
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[scheduled_master] invalid cron {cron_expr!r}: {exc} — "
            "falling back to +1h"
        )
        from datetime import timedelta

        return datetime.now(timezone.utc) + timedelta(hours=1)


@DBOS.scheduled("* * * * *")  # every minute
@DBOS.workflow()
async def scheduled_master_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """One tick. Counters are logged at INFO when there's actual work
    so a quiet system doesn't spam the log."""
    counters = await fire_due_schedules_step()
    if counters.get("fired") or counters.get("errors"):
        logger.info(f"[scheduled_master] tick: {counters}")


__all__ = [
    "fire_due_schedules_step",
    "scheduled_master_workflow",
]
