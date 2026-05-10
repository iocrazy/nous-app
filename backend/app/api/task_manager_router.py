# app/api/task_manager_router.py

"""
Task Manager Router

Unified task center API: list, cancel, retry, delete, clear completed tasks.
Covers all task types: download, upload, transcode, ai_pipeline, ai_extract, ai_transcription, ai_summary.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.services.infra.unified_task_manager import get_task_manager

router = APIRouter(prefix="/task-manager")


@router.get("/tasks")
async def list_tasks(
    auth: AuthDep,
    task_type: Optional[str] = Query(
        None,
        pattern="^(parse|download|upload|transcode|ai_pipeline|ai_extract|ai_transcription|ai_summary)$",
    ),
    status: Optional[str] = Query(
        None, pattern="^(pending|processing|completed|failed|cancelled)$"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get paginated tasks for the current user."""
    tracker = get_task_manager()
    tasks = await tracker.get_tasks(
        auth.user_id,
        task_type=task_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"success": True, "data": tasks}


@router.get("/tasks/active")
async def get_active_tasks(auth: AuthDep):
    """Get active (pending/processing) tasks — used by TopBar panel on init."""
    tracker = get_task_manager()
    tasks = await tracker.get_active_tasks(auth.user_id)
    return {"success": True, "data": tasks}


@router.get("/tasks/stats")
async def get_task_stats(auth: AuthDep):
    """Get task counts grouped by type and status."""
    tracker = get_task_manager()
    stats = await tracker.get_stats(auth.user_id)
    return {"success": True, "data": stats}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, auth: AuthDep):
    """Cancel a pending/processing task and revoke its Celery job."""
    tracker = get_task_manager()
    try:
        await tracker.cancel(task_id, auth.user_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
        raise HTTPException(500, f"Failed to cancel task: {e}")


# ── Workflow health classifier user controls (D8-B) ────────────────
# Match the columns added in migration 201. The sweeper respects all
# three: do_not_auto_cancel makes the row immune to ORPHAN_PENDING /
# USER_TIMEOUT auto-actions; max_duration_minutes is the user-set hard
# cap (overrides the type-policy ceiling); expected_duration_minutes
# silences the "running long" notification but doesn't change kill
# semantics.


class HealthOverridePayload(BaseModel):
    """Subset of task_tracking columns the user can override."""

    max_duration_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        le=43200,  # 30 days max — anything beyond is a UI bug
        description="User-set hard cap on workflow runtime. Exceeding it "
        "auto-cancels (status=timed_out). NULL = use type-policy ceiling.",
    )
    expected_duration_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        le=43200,
        description="User declares this task is expected to be slow. "
        "Suppresses the 'running long' notification but does not change "
        "auto-cancel behavior.",
    )
    do_not_auto_cancel: Optional[bool] = Field(
        default=None,
        description="When true, the sweeper never auto-cancels this row "
        "regardless of classification. Even ORPHAN_PENDING / USER_TIMEOUT "
        "only emit a notification.",
    )


@router.patch("/tasks/{task_id}/health-override")
async def patch_health_override(
    task_id: str, payload: HealthOverridePayload, auth: AuthDep
):
    """Update the user-control fields used by the workflow health sweeper.

    Returns the patched row so the frontend can refresh its local state
    without a follow-up GET.
    """
    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")

    try:
        result = await (
            sb.table("task_tracking")
            .update(fields)
            .eq("id", task_id)
            .eq("user_id", str(auth.user_id))
            .execute()
        )
    except Exception as e:
        logger.exception(f"health override patch failed for task {task_id}: {e}")
        raise HTTPException(500, f"update failed: {e}")
    if not result.data:
        raise HTTPException(404, "task not found or not owned by user")
    return {"success": True, "task": result.data[0]}


@router.post("/tasks/{task_id}/extend")
async def extend_task_timeout(task_id: str, auth: AuthDep, minutes: int = 30):
    """Convenience endpoint for the 'extend timeout' button.

    Equivalent to PATCH /health-override with max_duration_minutes set
    to (current elapsed + ``minutes``). If the row has no started_at the
    request just sets max to ``minutes``.
    """
    if minutes < 1 or minutes > 43200:
        raise HTTPException(400, "minutes must be between 1 and 43200")

    from datetime import datetime, timezone

    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    row_resp = await (
        sb.table("task_tracking")
        .select("started_at, max_duration_minutes")
        .eq("id", task_id)
        .eq("user_id", str(auth.user_id))
        .single()
        .execute()
    )
    row = row_resp.data
    if not row:
        raise HTTPException(404, "task not found")

    started = row.get("started_at")
    new_max = minutes
    if started:
        try:
            started_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            elapsed_min = int(
                (datetime.now(timezone.utc) - started_dt).total_seconds() / 60
            )
            new_max = elapsed_min + minutes
        except (ValueError, TypeError):
            pass

    await (
        sb.table("task_tracking")
        .update({"max_duration_minutes": new_max})
        .eq("id", task_id)
        .eq("user_id", str(auth.user_id))
        .execute()
    )
    return {"success": True, "max_duration_minutes": new_max}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, auth: AuthDep):
    """Reset a failed/cancelled task for retry and re-dispatch the Celery job."""
    tracker = get_task_manager()
    task = await tracker.retry_task(task_id, auth.user_id)
    if not task:
        raise HTTPException(404, "Task not found or not in retryable state")

    # Re-dispatch the actual Celery task based on type
    task_type = task.get("task_type")
    resource_id = task.get("resource_id")
    user_id = auth.user_id

    try:
        if task_type == "transcode" and resource_id:
            version_id = (task.get("metadata") or {}).get("version_id")
            if not version_id:
                # Fallback: look up current version from resource
                from app.repositories.resources_repository import ResourcesRepository

                repo = ResourcesRepository()
                versions = await repo.get_versions(resource_id)
                if versions:
                    version_id = str(versions[0]["id"])
            if version_id:
                from app.services.infra.dbos_orchestrator import start_workflow_routed
                from app.workflows.transcode import transcode_workflow

                await start_workflow_routed(
                    "transcode",
                    dbos_workflow_callable=transcode_workflow,
                    dbos_workflow_kwargs={
                        "resource_id": resource_id,
                        "version_id": version_id,
                        "user_id": user_id,
                    },
                )
                logger.info(
                    f"[TaskRetry] Dispatched transcode for resource={resource_id}, version={version_id}, reusing task={task_id}"
                )
            else:
                logger.warning(
                    f"[TaskRetry] No version found for resource={resource_id}, skipping dispatch"
                )

        elif task_type == "download":
            # Re-dispatch the download. The reaper marks zombie pending
            # tasks as failed (status=failed, error_code=WORKER_LOST),
            # and this path rehydrates the DBOS workflow from the media row.
            from app.repositories.media_repository import MediaRepository
            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.download import download_workflow

            media_id = task.get("media_id")
            if not media_id:
                logger.warning(f"[TaskRetry] Download task {task_id} has no media_id")
                return {"success": True, "data": task}

            mrepo = MediaRepository()
            media = await mrepo.get_by_platform_id(media_id)
            if not media:
                logger.warning(
                    f"[TaskRetry] Media not found for retry of {task_id}: {media_id}"
                )
                return {"success": True, "data": task}

            meta = task.get("metadata") or {}
            subtitle = task.get("subtitle") or ""
            want_video = (
                "Image" in subtitle
                or "Video" in subtitle
                or int(media.get("media_type") or 0) in (0, 2, 4, 61, 68)
            )
            want_cover = "Cover" in subtitle

            await start_workflow_routed(
                "download",
                dbos_workflow_callable=download_workflow,
                dbos_workflow_kwargs={
                    "platform_id": media_id,
                    "user_id": user_id,
                    "url": None,  # Douyin path
                    "download_video": want_video,
                    "download_cover": want_cover or True,
                    "media_type": int(media.get("media_type") or 0),
                    "video_title": media.get("title") or media_id,
                    "resource_id": resource_id,
                    "user_agent": meta.get("user_agent"),
                },
            )
            logger.info(
                f"[TaskRetry] Re-dispatched download for task={task_id}, media={media_id}"
            )

        elif task_type == "ai_summary" and resource_id:
            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.ai_summary import ai_summary_workflow

            media_id = task.get("media_id")
            try:
                await start_workflow_routed(
                    "ai_summary",
                    dbos_workflow_callable=ai_summary_workflow,
                    dbos_workflow_kwargs={
                        "parsed_media_id": int(media_id),
                        "user_id": user_id,
                    },
                )
            except Exception as _e:
                logger.debug(f"[TaskRetry] Failed to link dbos_workflow_id: {_e}")
            logger.info(f"[TaskRetry] Re-dispatched summary for task={task_id}")

    except Exception as e:
        logger.error(
            f"[TaskRetry] Failed to dispatch {task_type} for task {task_id}: {e}"
        )

    return {"success": True, "data": task}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, auth: AuthDep):
    """Delete a task record."""
    tracker = get_task_manager()
    deleted = await tracker.delete_task(task_id, auth.user_id)
    if not deleted:
        raise HTTPException(404, "Task not found")
    return {"success": True}


@router.post("/tasks/clear-completed")
async def clear_completed(auth: AuthDep):
    """Delete old completed/failed/cancelled tasks, keeping the 50 most recent."""
    tracker = get_task_manager()
    count = await tracker.clear_completed(auth.user_id)
    return {"success": True, "cleared": count}


@router.get("/tasks/{task_id}/progress")
async def get_task_progress(task_id: str, auth: AuthDep):
    """Get real-time download progress from Redis.

    Looks up the dbos_workflow_id from task_tracking, then reads the
    Redis key ``download_progress:{dbos_workflow_id}`` for live progress.
    Falls back to the task's DB progress if no Redis data exists.
    """
    import json

    tracker = get_task_manager()
    client = await tracker._get_client()

    # Look up the task by dbos_workflow_id (PK after migration 180).
    result = await (
        client.table("task_tracking")
        .select("dbos_workflow_id, progress, status, speed, total_bytes, error_msg")
        .eq("dbos_workflow_id", task_id)
        .eq("user_id", auth.user_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(404, "Task not found")

    task_row = result.data
    celery_id = task_row.get("dbos_workflow_id")

    # Try Redis first for real-time progress
    if celery_id:
        try:
            from app.core.redis import get_sync_redis

            redis_client = get_sync_redis()
            raw = redis_client.get(f"download_progress:{celery_id}")
            if raw:
                data = json.loads(raw)
                return {
                    "task_id": task_id,
                    "dbos_workflow_id": celery_id,
                    "status": data.get("status", "downloading"),
                    "percent": data.get("percent", 0),
                    "downloaded": data.get("downloaded", 0),
                    "total": data.get("total", 0),
                    "speed": data.get("speed", "0 B/s"),
                    "error": data.get("error"),
                }
        except Exception as e:
            logger.debug(f"Redis progress lookup failed: {e}")

    # Fallback to DB progress
    return {
        "task_id": task_id,
        "dbos_workflow_id": celery_id,
        "status": task_row.get("status", "pending"),
        "percent": task_row.get("progress", 0),
        "speed": task_row.get("speed"),
        "total": task_row.get("total_bytes"),
        "error": task_row.get("error_msg"),
    }
