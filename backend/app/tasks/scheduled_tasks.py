# app/tasks/scheduled_tasks.py

"""
Scheduled Tasks Module

Contains scheduled tasks dispatched by Celery Beat.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

from celery import shared_task
from loguru import logger

from app.core.enums import DownloadStatus
from app.tasks.utils import run_async


@shared_task
def cleanup_temp_files():
    """
    Clean up temporary files

    Delete temporary files and empty directories older than 7 days.
    Runs once daily.
    """
    logger.info("[Celery Beat] Starting temp file cleanup...")

    try:
        from app.core.utils import Utils

        # Get download base path
        try:
            base_path = Path(Utils.get_download_base_path())
        except ValueError:
            logger.warning(
                "[Celery Beat] Download path not configured, skipping cleanup"
            )
            return {"status": "skipped", "message": "Download path not configured"}

        if not base_path.exists():
            return {"status": "skipped", "message": "Download directory does not exist"}

        # Cleanup statistics
        files_deleted = 0
        dirs_deleted = 0
        space_freed = 0
        cutoff_time = datetime.now() - timedelta(days=7)

        # Traverse directory to clean temp files
        for root, dirs, files in os.walk(base_path, topdown=False):
            root_path = Path(root)

            # Delete temp files (.tmp, .part, .downloading)
            for file in files:
                file_path = root_path / file
                if file.endswith((".tmp", ".part", ".downloading")):
                    try:
                        file_stat = file_path.stat()
                        if datetime.fromtimestamp(file_stat.st_mtime) < cutoff_time:
                            space_freed += file_stat.st_size
                            file_path.unlink()
                            files_deleted += 1
                            logger.debug(f"Deleted temp file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete file {file_path}: {e}")

            # Delete empty directories
            for dir_name in dirs:
                dir_path = root_path / dir_name
                try:
                    if dir_path.is_dir() and not any(dir_path.iterdir()):
                        dir_path.rmdir()
                        dirs_deleted += 1
                        logger.debug(f"Deleted empty directory: {dir_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete directory {dir_path}: {e}")

        result = {
            "status": "success",
            "files_deleted": files_deleted,
            "dirs_deleted": dirs_deleted,
            "space_freed_mb": round(space_freed / (1024 * 1024), 2),
        }

        logger.success(
            f"[Celery Beat] Cleanup complete: deleted {files_deleted} files, "
            f"{dirs_deleted} empty directories, freed {result['space_freed_mb']} MB"
        )

        return result

    except Exception as e:
        logger.error(f"[Celery Beat] Temp file cleanup failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def retry_failed_downloads():
    """
    Retry failed downloads

    Find failed download records and resubmit download tasks.
    Runs every hour.
    """
    logger.info("[Celery Beat] Starting retry of failed downloads...")

    try:
        from app.repositories.media_repository import MediaRepository
        from app.tasks.download_tasks import download_unified_task

        repo = MediaRepository()

        # Get failed downloads (max 50)
        failed_videos = run_async(
            repo.get_pending_downloads(status=DownloadStatus.FAILED, limit=50)
        )

        if not failed_videos:
            logger.info("[Celery Beat] No downloads to retry")
            return {"status": "success", "retried": 0}

        retried_count = 0

        for video in failed_videos:
            platform_id = video.get("platform_id")
            media_type = video.get("media_type", 0)
            user_id = video.get("user_id")

            try:
                # Reset status to PENDING
                run_async(
                    repo.update(
                        platform_id,
                        {
                            "video_download_status": DownloadStatus.PENDING.value,
                            "error_message": None,
                        },
                    )
                )

                # Submit unified download task
                download_unified_task.delay(
                    platform_id,
                    user_id,
                    download_video=True,
                    download_cover=True,
                    media_type=int(media_type),
                )

                retried_count += 1
                logger.debug(f"Resubmitted download task: {platform_id}")

            except Exception as e:
                logger.warning(f"Failed to retry download {platform_id}: {e}")

        result = {
            "status": "success",
            "total_failed": len(failed_videos),
            "retried": retried_count,
        }

        logger.success(
            f"[Celery Beat] Retry complete: {retried_count}/{len(failed_videos)} tasks resubmitted"
        )

        return result

    except Exception as e:
        logger.error(f"[Celery Beat] Retry failed downloads error: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def update_statistics():
    """
    Update statistics data

    Calculate and cache various statistics.
    Runs every 6 hours.
    """
    logger.info("[Celery Beat] Starting statistics update...")

    try:
        from app.repositories.media_repository import MediaRepository

        repo = MediaRepository()

        # Get global statistics
        stats = run_async(repo.get_statistics())

        result = {
            "status": "success",
            "updated_at": datetime.now().isoformat(),
            "statistics": stats,
        }

        logger.success(f"[Celery Beat] Statistics update complete: {stats}")

        return result

    except Exception as e:
        logger.error(f"[Celery Beat] Statistics update failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def update_system_status():
    """
    Update system_status in Supabase (triggers Realtime broadcast).

    Collects queue, storage, network, workers, and active tasks metrics,
    then upserts into the single-row system_status table.
    Runs every 30 seconds via Celery Beat.
    """
    logger.debug("[Celery Beat] Updating system_status...")

    try:
        from app.db.supabase_client import get_async_supabase_admin
        from app.services.system_monitor_service import (
            get_active_tasks,
            get_network_status,
            get_queue_status,
            get_storage_status,
            get_worker_stats,
        )

        data = {
            "id": "00000000-0000-0000-0000-000000000001",
            "queue": get_queue_status(),
            "storage": get_storage_status(),
            "network": get_network_status(),
            "workers": get_worker_stats(),
            "active_tasks": get_active_tasks(),
        }

        async def _upsert():
            supabase = await get_async_supabase_admin()
            await supabase.table("system_status").upsert(data).execute()

        run_async(_upsert())

        logger.debug("[Celery Beat] system_status updated successfully")
        return {"status": "success"}

    except Exception as e:
        logger.error(f"[Celery Beat] update_system_status failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def reset_monthly_quotas():
    """
    Reset monthly quota usage for all members.

    Sets points_used_this_month to 0 and advances reset_at to the 1st of next month.
    Runs on the 1st of every month at 00:00.
    """
    logger.info("[Celery Beat] Starting monthly quota reset...")

    try:
        from app.db.supabase_client import get_async_supabase_admin

        async def _reset():
            supabase = await get_async_supabase_admin()

            # Calculate next month's reset date
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
            return response.data

        rows = run_async(_reset())
        count = len(rows) if rows else 0

        logger.success(
            f"[Celery Beat] Monthly quota reset complete: {count} rows reset"
        )
        return {"status": "success", "count": count}

    except Exception as e:
        logger.error(f"[Celery Beat] Monthly quota reset failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def reap_stuck_pending_tasks():
    """
    Mark zombie tasks as failed.

    Two things rot without this:

    1. unified_tasks: rows stuck in (pending/queued/no-started_at/age>1h).
       Happens when the worker crashed before claiming, Redis dropped the
       message, or the broker restarted. Surfaces a Retry button once
       flipped to 'failed'.

    2. resources.{transcript,summary,visual_analysis}_status: rows stuck
       in 'pending' with no matching live unified_task. The card's AI
       icons otherwise show perpetual "in-progress" for tasks that
       already died hours ago.
    """
    logger.info("[Celery Beat] Reaping stuck pending tasks...")

    try:
        from app.db.supabase_client import get_async_supabase_admin

        async def _reap():
            supabase = await get_async_supabase_admin()
            cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
            now_iso = datetime.now().isoformat()

            # Pass 1: unified_tasks zombie rows
            result = (
                await supabase.table("unified_tasks")
                .update(
                    {
                        "status": "failed",
                        "phase": "failed",
                        "error_msg": (
                            "Worker never claimed this task — Celery message "
                            "was lost (worker crash / broker restart). "
                            "Use Retry to re-queue."
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

            # Pass 2: resource-level AI status zombies (1h+ pending with
            # no matching active unified_task). We run this in SQL to
            # avoid a per-row NOT-IN join; the predicate is identical to
            # the bulk UPDATE in migration 128.
            resources_reaped = 0
            for field in (
                "transcript_status",
                "summary_status",
                "visual_analysis_status",
            ):
                sql = f"""
                WITH live AS (
                  SELECT DISTINCT resource_id::text AS rid
                  FROM unified_tasks
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
                    # exec_sql RPC not present — skip this pass silently.
                    # Migration 128 already cleaned existing rows; future
                    # zombies will accumulate slowly.
                    break

            return tasks_reaped, resources_reaped

        tasks_reaped, resources_reaped = run_async(_reap())
        if tasks_reaped or resources_reaped:
            logger.warning(
                f"[Celery Beat] Reaper: {tasks_reaped} unified_tasks + "
                f"{resources_reaped} resource AI statuses flipped to failed"
            )
        else:
            logger.info("[Celery Beat] Reaper found no stuck records")
        return {
            "status": "success",
            "tasks_reaped": tasks_reaped,
            "resources_reaped": resources_reaped,
        }

    except Exception as e:
        logger.error(f"[Celery Beat] Reaper failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def cleanup_old_unified_tasks():
    """
    Clean up old completed/failed/cancelled unified tasks.

    Removes tasks older than 7 days that are in terminal state.
    Runs once daily.
    """
    logger.info("[Celery Beat] Starting unified_tasks cleanup...")

    try:
        from app.db.supabase_client import get_async_supabase_admin

        async def _cleanup():
            supabase = await get_async_supabase_admin()
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            result = (
                await supabase.table("unified_tasks")
                .delete()
                .in_("status", ["completed", "failed", "cancelled"])
                .lt("updated_at", cutoff)
                .execute()
            )
            return len(result.data) if result.data else 0

        deleted = run_async(_cleanup())
        logger.success(
            f"[Celery Beat] Unified tasks cleanup: {deleted} old tasks removed"
        )
        return {"status": "success", "deleted": deleted}

    except Exception as e:
        logger.error(f"[Celery Beat] Unified tasks cleanup failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def health_check():
    """
    Health check task

    Check status of system components.
    Can be used for monitoring alerts.
    """
    logger.info("[Celery Beat] Running health check...")

    checks = {
        "celery": "ok",
        "redis": "unknown",
        "supabase": "unknown",
        "storage": "unknown",
    }

    # Check Redis
    try:
        from app.celery_app import celery_app

        celery_app.control.ping(timeout=5)
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)[:50]}"

    # Check Supabase
    try:
        from app.repositories.media_repository import MediaRepository

        repo = MediaRepository()
        # Simple query to test connection
        run_async(repo.get_statistics())
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {str(e)[:50]}"

    # Check storage directory
    try:
        from app.core.utils import Utils

        base_path = Path(Utils.get_download_base_path())
        if base_path.exists() and os.access(base_path, os.W_OK):
            checks["storage"] = "ok"
        else:
            checks["storage"] = "not writable"
    except ValueError:
        checks["storage"] = "not configured"
    except Exception as e:
        checks["storage"] = f"error: {str(e)[:50]}"

    # Determine overall status
    all_ok = all(v == "ok" for v in checks.values())
    status = "healthy" if all_ok else "degraded"

    result = {
        "status": status,
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }

    if all_ok:
        logger.success("[Celery Beat] Health check passed")
    else:
        logger.warning(f"[Celery Beat] Health check found issues: {checks}")

    return result


@shared_task(name="app.tasks.scheduled_tasks.recover_stale_orchestrator_locks")
def recover_stale_orchestrator_locks():
    """Recover orphaned dedup locks by checking unified_tasks.

    Scans for tasks stuck in 'processing' phase for more than 1 hour
    and marks them as failed with NETWORK_TIMEOUT error code.
    Runs every hour via Celery Beat.
    """
    logger.info("[Celery Beat] Starting stale orchestrator lock recovery...")

    try:
        from datetime import timezone

        from app.services.unified_task_manager import get_task_manager

        async def _recover():
            mgr = get_task_manager()
            client = await mgr._get_client()

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

            stale = await (
                client.table("unified_tasks")
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
                    logger.info(f"[Recovery] Marked stale task {task['id']} as failed")
                except Exception as e:
                    logger.warning(
                        f"[Recovery] Failed to recover task {task['id']}: {e}"
                    )

            return recovered

        count = run_async(_recover())
        logger.success(
            f"[Celery Beat] Stale lock recovery complete: {count} tasks recovered"
        )
        return {"status": "success", "recovered": count}

    except Exception as e:
        logger.error(f"[Celery Beat] Stale lock recovery failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def grant_daily_free_points():
    """Grant daily free points to each active user's team."""
    logger.info("[Celery Beat] Starting daily free points grant...")

    try:
        from app.core.config import settings
        from app.db.supabase_client import get_async_supabase_admin
        from app.services.points_service import PointsService

        amount = settings.DAILY_FREE_POINTS
        if amount <= 0:
            return {"status": "skipped", "reason": "DAILY_FREE_POINTS <= 0"}

        async def _grant():
            supabase = await get_async_supabase_admin()
            today = datetime.now().strftime("%Y-%m-%d")

            # Only grant to personal teams (is_personal=true)
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

                # Check if already granted today (prevent duplicates)
                existing = (
                    await supabase.table("daily_point_gifts")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("gift_date", today)
                    .maybe_single()
                    .execute()
                )
                if existing.data:
                    skipped += 1
                    continue

                # Add points to team balance
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

            return {"status": "success", "granted": granted, "skipped": skipped}

        result = run_async(_grant())
        logger.success(f"[Celery Beat] Daily free points grant complete: {result}")
        return result

    except Exception as e:
        logger.error(f"[Celery Beat] Daily free points grant failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def reclaim_daily_free_points():
    """Reclaim unused daily gift points from previous day."""
    logger.info("[Celery Beat] Starting daily free points reclaim...")

    try:
        from app.db.supabase_client import get_async_supabase_admin
        from app.services.points_service import PointsService

        async def _reclaim():
            supabase = await get_async_supabase_admin()
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            # Get yesterday's un-reclaimed gift records
            gifts_resp = (
                await supabase.table("daily_point_gifts")
                .select("*")
                .eq("gift_date", yesterday)
                .eq("status", "granted")
                .execute()
            )
            gifts = gifts_resp.data or []

            if not gifts:
                return {"status": "success", "reclaimed_count": 0, "total_reclaimed": 0}

            points_svc = PointsService()
            reclaimed_count = 0
            total_reclaimed = 0

            for gift in gifts:
                user_id = gift["user_id"]
                team_id = gift["team_id"]
                amount_granted = gift["amount_granted"]
                granted_at = gift["granted_at"]

                # Query consumption after the gift was granted
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

                # Calculate unused portion
                used = min(amount_granted, consumed)
                reclaim_amount = amount_granted - used

                # Reclaim unused points
                actual_reclaimed = 0
                if reclaim_amount > 0:
                    result = await points_svc.reclaim_daily_gift(
                        team_id=team_id,
                        amount=reclaim_amount,
                        user_id=user_id,
                        description=f"Reclaim unused daily gift ({yesterday})",
                    )
                    actual_reclaimed = result.get("reclaimed", 0)

                # Update gift record
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
                "status": "success",
                "reclaimed_count": reclaimed_count,
                "total_reclaimed": total_reclaimed,
            }

        result = run_async(_reclaim())
        logger.success(f"[Celery Beat] Daily free points reclaim complete: {result}")
        return result

    except Exception as e:
        logger.error(f"[Celery Beat] Daily free points reclaim failed: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def cleanup_trashed_resources():
    """Permanently delete trashed resources older than 15 days."""
    logger.info("[Celery Beat] Starting trashed resource cleanup...")
    try:
        from app.services.resources_service import ResourcesService

        svc = ResourcesService()
        cleaned = run_async(svc.cleanup_expired_trash(older_than_days=15))
        logger.success(
            f"[Celery Beat] Trashed resource cleanup done: {cleaned} deleted"
        )
        return {"status": "success", "cleaned": cleaned}
    except Exception as e:
        logger.error(f"[Celery Beat] Trashed resource cleanup failed: {e}")
        return {"status": "error", "error": str(e)}
