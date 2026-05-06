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

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
def retry_failed_downloads_step() -> dict[str, Any]:
    """Find FAILED downloads, reset to PENDING, re-dispatch download task.
    Skips orphan rows (user_id IS NULL) per CLAUDE.md regression notes.

    PR-D7 phase 2: dispatch goes through start_workflow_routed so the
    routing table picks DBOS / celery / shadow per task_type."""
    from app.core.enums import DownloadStatus
    from app.repositories.media_repository import MediaRepository
    from app.services.dbos_orchestrator import start_workflow_routed
    from app.workflows.download import download_workflow

    async def _do() -> dict[str, int]:
        repo = MediaRepository()
        failed = await repo.get_pending_downloads(
            status=DownloadStatus.FAILED, limit=50
        )
        if not failed:
            return {"total_failed": 0, "retried": 0, "skipped_orphan": 0}

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
            "total_failed": len(failed),
            "retried": retried,
            "skipped_orphan": skipped_orphan,
        }

    result = asyncio.run(_do())
    return {"status": "success", **result}


@DBOS.step()
def reap_stuck_pending_tasks_step() -> dict[str, Any]:
    """Two passes: (1) flip queued task_tracking rows >1h old to failed,
    (2) flip resources.{ai_*}_status from pending to failed when no live
    matching unified_task exists."""
    from app.db.supabase_client import get_async_supabase_admin

    async def _do() -> tuple[int, int]:
        supabase = await get_async_supabase_admin()
        # Use timezone-aware UTC so the ISO string carries +00:00 and
        # PostgreSQL doesn't fall back to interpreting it in the
        # connection's local TZ. Naive `datetime.now()` was reaping
        # 25-second-old DBOS workflows because Mac local time +8h
        # made cutoff far in the future of any UTC timestamptz row.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()

        result = (
            await supabase.table("task_tracking")
            .update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error_msg": (
                        "Worker never claimed this task within 1h — "
                        "DBOS workflow may have crashed or never "
                        "executed. Use Retry to re-queue."
                    ),
                    "error_code": "WORKER_LOST",
                    "updated_at": now_iso,
                }
            )
            .eq("status", "pending")
            .eq("phase", "queued")
            .is_("started_at", "null")
            .lt("created_at", cutoff)
            .execute()
        )
        tasks_reaped = len(result.data) if result.data else 0

        resources_reaped = 0
        for field in ("transcript_status", "summary_status", "visual_analysis_status"):
            sql = f"""
            WITH live AS (
              SELECT DISTINCT resource_id::text AS rid
              FROM task_tracking
              WHERE status IN ('pending','processing','running')
                AND task_type IN ('ai_extract','ai_transcription','ai_summary','ai_pipeline','ai_visual_analysis')
                AND resource_id IS NOT NULL
            )
            UPDATE resources
               SET {field} = 'failed'
             WHERE {field} = 'pending'
               AND updated_at < NOW() - INTERVAL '1 hour'
               AND id::text NOT IN (SELECT rid FROM live)
             RETURNING id
            """
            try:
                r = await supabase.rpc("exec_sql", {"sql": sql}).execute()
                resources_reaped += len(r.data) if r and r.data else 0
            except Exception:
                # exec_sql RPC absent — skip silently (matches legacy behaviour).
                break

        return tasks_reaped, resources_reaped

    tasks_reaped, resources_reaped = asyncio.run(_do())
    return {
        "status": "success",
        "tasks_reaped": tasks_reaped,
        "resources_reaped": resources_reaped,
    }


@DBOS.step()
def recover_stale_orchestrator_locks_step() -> dict[str, Any]:
    """Release dedup locks held by task_tracking stuck in 'processing'
    >1h. Becomes obsolete in D3d (DBOS workflow_id replaces this)."""
    from app.services.unified_task_manager import get_task_manager

    async def _do() -> int:
        mgr = get_task_manager()
        client = await mgr._get_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        stale = await (
            client.table("task_tracking")
            .select("id, dedup_key")
            .eq("phase", "processing")
            .lt("started_at", cutoff)
            .execute()
        )

        recovered = 0
        for task in stale.data or []:
            try:
                await mgr.fail(
                    task["id"], "Stale task timeout", error_code="NETWORK_TIMEOUT"
                )
                if task.get("dedup_key"):
                    mgr.release_lock(task["dedup_key"])
                recovered += 1
            except Exception as e:
                logger.warning(
                    f"[recover_stale_orchestrator_locks] task {task['id']}: {e}"
                )
        return recovered

    count = asyncio.run(_do())
    return {"status": "success", "recovered": count}


@DBOS.scheduled("0 * * * *")  # hourly at :00
@DBOS.workflow()
def retry_failed_downloads_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = retry_failed_downloads_step()
    if result.get("retried") or result.get("skipped_orphan"):
        logger.info(f"[retry_failed_downloads] {result}")


@DBOS.scheduled("*/15 * * * *")  # every 15 minutes
@DBOS.workflow()
def reap_stuck_pending_tasks_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = reap_stuck_pending_tasks_step()
    if result.get("tasks_reaped") or result.get("resources_reaped"):
        logger.warning(f"[reap_stuck_pending_tasks] {result}")


@DBOS.scheduled("30 * * * *")  # hourly at :30 (offset from retry workflow)
@DBOS.workflow()
def recover_stale_orchestrator_locks_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = recover_stale_orchestrator_locks_step()
    if result.get("recovered"):
        logger.info(f"[recover_stale_orchestrator_locks] {result}")
