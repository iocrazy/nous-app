"""Admin API routes for Task Center management."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.schemas.admin import (
    AdminTaskResponse,
    AdminTaskListResponse,
    AdminTaskStatsResponse,
)
from app.utils.admin_helpers import create_audit_log, batch_get_user_auth_info


router = APIRouter()

VALID_STATUSES = {"pending", "processing", "completed", "failed", "cancelled"}
VALID_TASK_TYPES = {
    "parse", "download", "upload", "transcode",
    "ai_pipeline", "ai_extract", "ai_transcription", "ai_summary",
}


@router.get("/stats", response_model=AdminTaskStatsResponse)
async def get_task_stats(auth: AdminAuthDep):
    """Get task status distribution counts."""
    supabase = await get_async_supabase_admin()

    # Total count
    total_result = (
        await supabase.table("unified_tasks")
        .select("id", count="exact")
        .execute()
    )
    total = total_result.count or 0

    # Count by each status
    status_counts = {}
    for s in VALID_STATUSES:
        result = (
            await supabase.table("unified_tasks")
            .select("id", count="exact")
            .eq("status", s)
            .execute()
        )
        status_counts[s] = result.count or 0

    return AdminTaskStatsResponse(
        total=total,
        pending=status_counts.get("pending", 0),
        processing=status_counts.get("processing", 0),
        completed=status_counts.get("completed", 0),
        failed=status_counts.get("failed", 0),
        cancelled=status_counts.get("cancelled", 0),
    )


@router.get("", response_model=AdminTaskListResponse)
async def list_tasks(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    task_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
):
    """List all tasks with pagination, filtering, and user email lookup."""
    supabase = await get_async_supabase_admin()

    query = (
        supabase.table("unified_tasks")
        .select(
            "id, user_id, task_type, status, phase, title, subtitle, "
            "progress, speed, total_bytes, error_msg, error_code, "
            "resource_id, media_id, celery_task_id, metadata, "
            "created_at, started_at, completed_at",
            count="exact",
        )
    )

    # Filters
    if status_filter and status_filter in VALID_STATUSES:
        query = query.eq("status", status_filter)
    if task_type and task_type in VALID_TASK_TYPES:
        query = query.eq("task_type", task_type)
    if search:
        query = query.ilike("title", f"%{search}%")

    # Sorting
    valid_sort_fields = {"created_at", "started_at", "completed_at", "status"}
    if sort_by not in valid_sort_fields:
        sort_by = "created_at"
    desc = sort_order != "asc"
    query = query.order(sort_by, desc=desc)

    # Pagination
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()
    rows = result.data or []
    total = result.count or 0

    # Batch lookup user emails from auth.users
    user_ids = list({r["user_id"] for r in rows if r.get("user_id")})
    email_map: dict[str, str] = {}
    if user_ids:
        auth_info = await batch_get_user_auth_info(user_ids)
        email_map = {
            uid: email for uid, (email, _) in auth_info.items() if email
        }

    items = [
        AdminTaskResponse(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            user_email=email_map.get(str(row["user_id"])),
            task_type=row["task_type"],
            status=row["status"],
            phase=row.get("phase"),
            title=row.get("title") or "",
            subtitle=row.get("subtitle"),
            progress=row.get("progress") or 0,
            speed=row.get("speed"),
            total_bytes=row.get("total_bytes"),
            error_msg=row.get("error_msg"),
            error_code=row.get("error_code"),
            resource_id=str(row["resource_id"]) if row.get("resource_id") else None,
            media_id=str(row["media_id"]) if row.get("media_id") else None,
            celery_task_id=row.get("celery_task_id"),
            metadata=row.get("metadata"),
            created_at=row.get("created_at") or "",
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
        )
        for row in rows
    ]

    return AdminTaskListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Cancel a pending or processing task."""
    supabase = await get_async_supabase_admin()

    # Verify task exists and is cancellable
    result = (
        await supabase.table("unified_tasks")
        .select("id, status, celery_task_id")
        .eq("id", task_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    task = result.data
    if task["status"] not in ("pending", "processing"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel task with status '{task['status']}'",
        )

    # Revoke Celery task if applicable
    celery_task_id = task.get("celery_task_id")
    if celery_task_id:
        try:
            from app.celery_app import celery_app
            celery_app.control.revoke(celery_task_id, terminate=True)
        except Exception as e:
            logger.warning(f"[Admin] Failed to revoke Celery task {celery_task_id}: {e}")

    # Update task status
    await (
        supabase.table("unified_tasks")
        .update({"status": "cancelled", "phase": "cancelled"})
        .eq("id", task_id)
        .execute()
    )

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="task_cancel",
        target_type="unified_task",
        target_id=task_id,
        details={"celery_task_id": celery_task_id},
        ip_address=request.client.host if request.client else None,
    )

    logger.info(f"[Admin] Task cancelled: {task_id} by admin={auth.user_id}")
    return {"message": "Task cancelled", "task_id": task_id}


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Retry a failed task by resetting its status."""
    supabase = await get_async_supabase_admin()

    # Verify task exists and is retryable
    result = (
        await supabase.table("unified_tasks")
        .select("id, status, task_type")
        .eq("id", task_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    task = result.data
    if task["status"] != "failed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry task with status '{task['status']}', only failed tasks can be retried",
        )

    # Reset task status
    await (
        supabase.table("unified_tasks")
        .update({
            "status": "pending",
            "phase": "queued",
            "progress": 0,
            "error_msg": None,
            "error_code": None,
            "started_at": None,
            "completed_at": None,
        })
        .eq("id", task_id)
        .execute()
    )

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="task_retry",
        target_type="unified_task",
        target_id=task_id,
        details={"task_type": task["task_type"]},
        ip_address=request.client.host if request.client else None,
    )

    logger.info(f"[Admin] Task retry: {task_id} by admin={auth.user_id}")
    return {"message": "Task reset to pending", "task_id": task_id}
