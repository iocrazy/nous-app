# app/api/tasks_router.py

"""
Task Management API Router

Provides endpoints for monitoring and managing download tasks.
"""

from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.core.deps import AuthDep
from app.services.task_manager import get_task_manager, TaskStatus

router = APIRouter(prefix="/download-tasks")

TAGS = ["Tasks"]


# ========== Response Models ==========

class TaskItem(BaseModel):
    aweme_id: str
    video_title: str
    status: str
    percent: int
    downloaded: int
    total: int
    speed: str
    retry_count: int
    max_retries: int
    error: Optional[str]
    started_at: str
    updated_at: str


class TaskListResponse(BaseModel):
    items: List[TaskItem]
    total: int


class TaskStatsResponse(BaseModel):
    pending: int
    downloading: int
    completed: int
    failed: int
    worker_online: bool
    storage_free: str
    storage_used_percent: float


class RetryResponse(BaseModel):
    success: bool
    message: str
    aweme_id: Optional[str] = None


class RetryAllResponse(BaseModel):
    success: bool
    message: str
    retried_count: int
    failed_ids: List[str]


# ========== Endpoints ==========

@router.get("", tags=TAGS, response_model=TaskListResponse)
async def get_tasks(
    auth: AuthDep,
    status: Optional[str] = Query(None, description="Filter by status: pending, downloading, completed, failed"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Get list of download tasks.

    Supports filtering by status and pagination.
    """
    task_manager = get_task_manager()

    # Parse status filter
    status_filter = None
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Valid values: pending, downloading, completed, failed"
            )

    tasks = task_manager.get_tasks(status=status_filter, limit=limit, offset=offset)

    # Get total count
    all_tasks = task_manager.get_tasks(status=status_filter, limit=10000)
    total = len(all_tasks)

    return TaskListResponse(
        items=[TaskItem(**t) for t in tasks],
        total=total,
    )


@router.get("/stats", tags=TAGS, response_model=TaskStatsResponse)
async def get_task_stats(auth: AuthDep):
    """
    Get task queue statistics.

    Returns counts for each status, worker status, and storage info.
    """
    task_manager = get_task_manager()
    stats = task_manager.get_stats()

    # Check Celery worker status (quick check with short timeout)
    worker_online = False
    try:
        from app.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=0.5)  # Reduced timeout
        ping = inspect.ping()
        worker_online = bool(ping)
    except Exception as e:
        logger.debug(f"Worker check skipped: {e}")
        # Fallback: check if any celery process is running
        import subprocess
        try:
            result = subprocess.run(['pgrep', '-f', 'celery'], capture_output=True, timeout=1)
            worker_online = result.returncode == 0
        except Exception:
            pass

    # Get storage info
    storage_free = "Unknown"
    storage_used_percent = 0.0
    try:
        import shutil
        from app.core.config import settings

        if settings.NAS_BASE_PATH:
            usage = shutil.disk_usage(settings.NAS_BASE_PATH)
            free_gb = usage.free / (1024 ** 3)
            if free_gb >= 1024:
                storage_free = f"{free_gb / 1024:.1f} TB"
            else:
                storage_free = f"{free_gb:.1f} GB"
            storage_used_percent = round((usage.used / usage.total) * 100, 1)
    except Exception as e:
        logger.warning(f"Failed to get storage info: {e}")

    return TaskStatsResponse(
        pending=stats.get("pending", 0),
        downloading=stats.get("downloading", 0),
        completed=stats.get("completed", 0),
        failed=stats.get("failed", 0),
        worker_online=worker_online,
        storage_free=storage_free,
        storage_used_percent=storage_used_percent,
    )


@router.post("/{aweme_id}/retry", tags=TAGS, response_model=RetryResponse)
async def retry_task(
    auth: AuthDep,
    aweme_id: str,
    force: bool = Query(False, description="Force retry even if max retries reached"),
):
    """
    Retry a failed download task.

    Set force=true to reset retry count and retry anyway.
    """
    task_manager = get_task_manager()

    task = task_manager.get_task(aweme_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {aweme_id}")

    if task["status"] != TaskStatus.FAILED.value:
        return RetryResponse(
            success=False,
            message=f"Task is not failed, current status: {task['status']}",
            aweme_id=aweme_id,
        )

    # Reset for retry
    if force:
        task_manager.force_retry(aweme_id)
    else:
        if not task_manager.can_retry(aweme_id):
            return RetryResponse(
                success=False,
                message="Max retries reached. Use force=true to retry anyway.",
                aweme_id=aweme_id,
            )
        task_manager.reset_for_retry(aweme_id)

    # Trigger download task
    try:
        from app.tasks.download_tasks import download_media_task

        # Get video info from database
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
        repo = SupabaseDouyinRepository()
        video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)

        if video:
            download_media_task.delay(
                aweme_id=aweme_id,
                user_id=auth.user_id,
                download_video=True,
                download_cover=True,
                aweme_type=video.get("aweme_type", 0),
                video_title=video.get("video_title", "Unknown"),
            )
            return RetryResponse(
                success=True,
                message="Task queued for retry",
                aweme_id=aweme_id,
            )
        else:
            return RetryResponse(
                success=False,
                message="Video not found in database",
                aweme_id=aweme_id,
            )

    except Exception as e:
        logger.error(f"Failed to queue retry task: {e}")
        return RetryResponse(
            success=False,
            message=f"Failed to queue task: {str(e)}",
            aweme_id=aweme_id,
        )


@router.post("/retry-all", tags=TAGS, response_model=RetryAllResponse)
async def retry_all_failed(
    auth: AuthDep,
    force: bool = Query(False, description="Force retry even if max retries reached"),
):
    """
    Retry all failed download tasks.
    """
    task_manager = get_task_manager()

    failed_tasks = task_manager.get_failed_tasks()
    if not failed_tasks:
        return RetryAllResponse(
            success=True,
            message="No failed tasks to retry",
            retried_count=0,
            failed_ids=[],
        )

    retried = []
    failed_ids = []

    from app.tasks.download_tasks import download_media_task
    from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
    repo = SupabaseDouyinRepository()

    for task in failed_tasks:
        aweme_id = task["aweme_id"]

        # Check if can retry
        if not force and not task_manager.can_retry(aweme_id):
            failed_ids.append(aweme_id)
            continue

        # Reset for retry
        if force:
            task_manager.force_retry(aweme_id)
        else:
            task_manager.reset_for_retry(aweme_id)

        try:
            video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)
            if video:
                download_media_task.delay(
                    aweme_id=aweme_id,
                    user_id=auth.user_id,
                    download_video=True,
                    download_cover=True,
                    aweme_type=video.get("aweme_type", 0),
                    video_title=video.get("video_title", "Unknown"),
                )
                retried.append(aweme_id)
            else:
                failed_ids.append(aweme_id)
        except Exception as e:
            logger.error(f"Failed to queue retry for {aweme_id}: {e}")
            failed_ids.append(aweme_id)

    return RetryAllResponse(
        success=True,
        message=f"Retried {len(retried)} tasks",
        retried_count=len(retried),
        failed_ids=failed_ids,
    )


@router.delete("/{aweme_id}", tags=TAGS)
async def delete_task(auth: AuthDep, aweme_id: str):
    """
    Delete a task from the queue.
    """
    task_manager = get_task_manager()

    if task_manager.delete_task(aweme_id):
        return {"success": True, "message": f"Task {aweme_id} deleted"}
    else:
        raise HTTPException(status_code=404, detail=f"Task not found: {aweme_id}")


@router.post("/cleanup", tags=TAGS)
async def cleanup_completed_tasks(
    auth: AuthDep,
    keep_recent: int = Query(100, ge=0, le=1000),
):
    """
    Clean up old completed tasks, keeping the most recent ones.
    """
    task_manager = get_task_manager()
    removed = task_manager.cleanup_completed(keep_recent=keep_recent)

    return {
        "success": True,
        "message": f"Removed {removed} completed tasks",
        "removed_count": removed,
    }


@router.post("/recalculate-stats", tags=TAGS)
async def recalculate_stats(auth: AuthDep):
    """
    Recalculate task statistics from actual tasks.

    Use this if stats become out of sync.
    """
    task_manager = get_task_manager()
    stats = task_manager.recalculate_stats()

    return {
        "success": True,
        "message": "Stats recalculated",
        "stats": stats,
    }
