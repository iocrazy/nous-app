# app/api/task_manager_router.py

"""
Task Manager Router

Unified task center API: list, cancel, retry, delete, clear completed tasks.
Covers all task types: download, upload, transcode, ai_pipeline.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.services.task_tracker import get_task_tracker

router = APIRouter(prefix="/task-manager")


@router.get("/tasks")
async def list_tasks(
    auth: AuthDep,
    task_type: Optional[str] = Query(None, pattern="^(download|upload|transcode|ai_pipeline)$"),
    status: Optional[str] = Query(None, pattern="^(pending|processing|completed|failed|cancelled)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get paginated tasks for the current user."""
    tracker = get_task_tracker()
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
    tracker = get_task_tracker()
    tasks = await tracker.get_active_tasks(auth.user_id)
    return {"success": True, "data": tasks}


@router.get("/tasks/stats")
async def get_task_stats(auth: AuthDep):
    """Get task counts grouped by type and status."""
    tracker = get_task_tracker()
    stats = await tracker.get_stats(auth.user_id)
    return {"success": True, "data": stats}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, auth: AuthDep):
    """Cancel a pending/processing task and revoke its Celery job."""
    tracker = get_task_tracker()
    try:
        await tracker.cancel(task_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
        raise HTTPException(500, f"Failed to cancel task: {e}")


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, auth: AuthDep):
    """Reset a failed/cancelled task for retry."""
    tracker = get_task_tracker()
    task = await tracker.retry_task(task_id, auth.user_id)
    if not task:
        raise HTTPException(404, "Task not found or not in retryable state")
    return {"success": True, "data": task}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, auth: AuthDep):
    """Delete a task record."""
    tracker = get_task_tracker()
    deleted = await tracker.delete_task(task_id, auth.user_id)
    if not deleted:
        raise HTTPException(404, "Task not found")
    return {"success": True}


@router.post("/tasks/clear-completed")
async def clear_completed(auth: AuthDep):
    """Delete old completed/failed/cancelled tasks, keeping the 50 most recent."""
    tracker = get_task_tracker()
    count = await tracker.clear_completed(auth.user_id)
    return {"success": True, "cleared": count}
