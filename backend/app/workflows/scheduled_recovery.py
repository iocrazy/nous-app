"""Scheduled recovery workflows — port of three rescue jobs from
`tasks.scheduled_tasks`.

  - retry_failed_downloads (hourly) — resubmit FAILED downloads
  - reap_stuck_pending_tasks (every 15min) — flip zombie task_tracking
    + resource AI status to 'failed'
  - recover_stale_orchestrator_locks (hourly) — release dedup locks
    held by dead processing tasks

⚠️ Important callouts (per CLAUDE.md memory + _DEFERRED_TASKS.md):
  - retry_failed_downloads MUST skip rows with user_id IS NULL —
    legacy/system-initiated downloads have no user attribution and
    spam user_logs/user_settings repos with 23502/22P02 errors.
  - recover_stale_orchestrator_locks becomes obsolete in D3d once
    DBOS workflow_id replaces the orchestrator dedup lock model.
    Kept here so the celery-beat schedule can be removed safely
    while D3d is still in flight.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def retry_failed_downloads_step() -> dict[str, Any]:
    """Find FAILED downloads, reset to PENDING, re-dispatch download task.
    Skips orphan rows (user_id IS NULL) per CLAUDE.md regression notes.

    PR-D7 phase 2: dispatch goes through start_workflow_routed so the
    routing table picks DBOS / celery / shadow per task_type."""
    from app.core.enums import DownloadStatus
    from app.repositories.media_repository import MediaRepository
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.workflows.download import download_workflow

    repo = MediaRepository()
    failed = await repo.get_pending_downloads(status=DownloadStatus.FAILED, limit=50)
    if not failed:
        return {
            "status": "success",
            "total_failed": 0,
            "retried": 0,
            "skipped_orphan": 0,
        }

    retried = 0
    skipped_orphan = 0
    for video in failed:
        platform_id = video.get("platform_id")
        media_type = video.get("media_type", 0)
        user_id = video.get("user_id")

        if not user_id:
            skipped_orphan += 1
            continue

        try:
            await repo.update(
                platform_id,
                {
                    "video_download_status": DownloadStatus.PENDING.value,
                    "error_message": None,
                },
            )
            await start_workflow_routed(
                "download",
                dbos_workflow_callable=download_workflow,
                dbos_workflow_kwargs={
                    "platform_id": platform_id,
                    "user_id": user_id,
                    "download_video": True,
                    "download_cover": True,
                    "media_type": int(media_type),
                },
            )
            retried += 1
        except Exception as e:
            logger.warning(f"[retry_failed_downloads] {platform_id}: {e}")

    return {
        "status": "success",
        "total_failed": len(failed),
        "retried": retried,
        "skipped_orphan": skipped_orphan,
    }


@DBOS.step()
async def reap_stuck_pending_tasks_step() -> dict[str, Any]:
    """Two passes: (1) flip queued task_tracking rows >1h old to failed,
    (2) flip resources.{ai_*}_status from pending to failed when no live
    matching unified_task exists."""
    # Direct PG via the SQLAlchemy engine (no httpx). tz-aware UTC so
    # timestamptz comparisons don't fall back to the connection's local TZ.
    from app.db import engine as db_engine

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    now_dt = datetime.now(timezone.utc)

    # status/phase 'lost' on orphaned tasks: a deliberate "writer of last
    # resort" exception to the trigger-owns-phase rule — the DBOS lifecycle
    # trigger never fires for tasks the worker never claimed.
    tasks_reaped = await db_engine.execute(
        "UPDATE public.task_tracking SET status = 'lost', phase = 'lost', "
        "error_msg = :msg, error_code = 'WORKER_LOST', updated_at = :now "
        "WHERE status = 'pending' AND phase = 'queued' "
        "AND started_at IS NULL AND created_at < :cutoff",
        {
            "msg": (
                "Worker never claimed this task within 1h — DBOS workflow "
                "may have crashed or never executed. Use Retry to re-queue."
            ),
            "now": now_dt,
            "cutoff": cutoff,
        },
    )

    resources_reaped = 0
    for field in ("transcript_status", "summary_status", "visual_analysis_status"):
        # field is one of three hardcoded column names (not user input).
        # The engine runs raw SQL directly — no exec_sql RPC wrapper needed.
        sql = f"""
        WITH live AS (
          SELECT DISTINCT resource_id::text AS rid
          FROM public.task_tracking
          WHERE status IN ('pending','processing','running')
            AND task_type IN ('ai_extract','ai_transcription','ai_summary','ai_pipeline','ai_visual_analysis')
            AND resource_id IS NOT NULL
        )
        UPDATE public.resources
           SET {field} = 'failed'
         WHERE {field} = 'pending'
           AND updated_at < NOW() - INTERVAL '1 hour'
           AND id::text NOT IN (SELECT rid FROM live)
        """
        try:
            resources_reaped += await db_engine.execute(sql)
        except Exception:
            break

    return {
        "status": "success",
        "tasks_reaped": tasks_reaped,
        "resources_reaped": resources_reaped,
    }


@DBOS.step()
async def recover_stale_orchestrator_locks_step() -> dict[str, Any]:
    """Release dedup locks held by task_tracking stuck in 'processing'.

    Per-type ceiling (D11 / agent_framework.workflow_timeout_policy):
    parse>5min, download>30min, ai_visual_analysis>60min etc. Avoids
    the previous one-size-fits-all 1h cutoff that was both too eager
    for parse (real failure at 5min) and too lax for analyze (legit at
    30min, was reaped wrongly).

    Becomes obsolete in D3d (DBOS workflow_id replaces this)."""
    from app.agent_framework import is_stuck
    from app.services.infra.unified_task_manager import get_task_manager

    mgr = get_task_manager()
    from app.db import engine as db_engine

    # Pull a generous window — 2h covers the longest configured ceiling
    # (ai_visual_analysis = 60min) plus headroom; per-row filtering by
    # task_type ceiling happens below. tz-aware datetimes (not isoformat
    # strings) so the engine binds them as timestamptz.
    broad_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    stale = await db_engine.fetch_all(
        "SELECT dbos_workflow_id, dedup_key, task_type, started_at "
        "FROM public.task_tracking WHERE phase = 'processing' "
        "AND started_at < :cutoff",
        {"cutoff": broad_cutoff},
    )

    # Also catch tasks past their per-type ceiling but inside the broad
    # cutoff. Two-window query: broad + narrow per type.
    narrow_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    narrow = await db_engine.fetch_all(
        "SELECT dbos_workflow_id, dedup_key, task_type, started_at "
        "FROM public.task_tracking WHERE phase = 'processing' "
        "AND started_at < :cutoff",
        {"cutoff": narrow_cutoff},
    )

    # Dedup the union by dbos_workflow_id
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for row in stale + narrow:
        wid = row.get("dbos_workflow_id")
        if wid and wid not in seen:
            seen.add(wid)
            candidates.append(row)

    now_utc = datetime.now(timezone.utc)
    recovered = 0
    for task in candidates:
        tid = task.get("dbos_workflow_id")
        task_type = task.get("task_type") or ""
        # The engine returns started_at as a tz-aware datetime; tolerate a
        # raw ISO string too in case a row was written by the REST path.
        started_at_val = task.get("started_at")
        elapsed = 0.0
        if started_at_val:
            try:
                if isinstance(started_at_val, str):
                    started_dt = datetime.fromisoformat(
                        started_at_val.replace("Z", "+00:00")
                    )
                else:
                    started_dt = started_at_val
                elapsed = (now_utc - started_dt).total_seconds()
            except (ValueError, TypeError):
                elapsed = 0.0

        if not is_stuck(task_type, elapsed_seconds=elapsed):
            # Within ceiling — leave alone
            continue

        try:
            if tid:
                # Use mark_lost (Sprint 2): system-level orphan / worker
                # died, NOT business-level failure. Operators can query
                # status='lost' GROUP BY task_type to spot infra issues
                # vs application bugs.
                await mgr.mark_lost(
                    tid,
                    f"Stuck {task_type} timeout (elapsed {int(elapsed)}s, "
                    f"ceiling per workflow_timeout_policy)",
                    error_code="WORKER_LOST",
                )
            if task.get("dedup_key"):
                mgr.release_lock(task["dedup_key"])
            recovered += 1
        except Exception as e:
            logger.warning(f"[recover_stale_orchestrator_locks] task {tid}: {e}")
    return {"status": "success", "recovered": recovered}


@DBOS.scheduled("0 * * * *")  # hourly at :00
@DBOS.workflow()
async def retry_failed_downloads_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await retry_failed_downloads_step()
    if result.get("retried") or result.get("skipped_orphan"):
        logger.info(f"[retry_failed_downloads] {result}")


@DBOS.scheduled("*/15 * * * *")  # every 15 minutes
@DBOS.workflow()
async def reap_stuck_pending_tasks_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await reap_stuck_pending_tasks_step()
    if result.get("tasks_reaped") or result.get("resources_reaped"):
        logger.warning(f"[reap_stuck_pending_tasks] {result}")


@DBOS.scheduled("30 * * * *")  # hourly at :30 (offset from retry workflow)
@DBOS.workflow()
async def recover_stale_orchestrator_locks_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = await recover_stale_orchestrator_locks_step()
    if result.get("recovered"):
        logger.info(f"[recover_stale_orchestrator_locks] {result}")
