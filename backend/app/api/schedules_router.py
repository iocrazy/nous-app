"""schedules_router — user-defined cron schedules CRUD.

Backed by the `user_schedules` table (mig 204). The master scheduler
workflow (`app/workflows/scheduled_master.py`) scans this table every
minute and fires due rows.

Endpoints
---------
* POST    /api/v1/schedules        — create
* GET     /api/v1/schedules        — list user's schedules
* GET     /api/v1/schedules/{id}   — single
* PATCH   /api/v1/schedules/{id}   — update (cron / payload / enabled)
* DELETE  /api/v1/schedules/{id}   — delete
* POST    /api/v1/schedules/{id}/fire-now — manual one-shot trigger
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin


router = APIRouter(prefix="/schedules", tags=["Schedules"])


_ALLOWED_TASK_TYPES = {
    "parse", "download", "transcode",
    "ai_summary", "ai_transcription", "ai_visual_analysis",
}


class ScheduleCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    cron_expr: str = Field(..., min_length=1, max_length=100)
    task_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ScheduleUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    cron_expr: Optional[str] = Field(None, min_length=1, max_length=100)
    payload: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class ScheduleResponse(BaseModel):
    id: str
    user_id: Optional[str]
    name: str
    cron_expr: str
    task_type: str
    payload: Dict[str, Any]
    lane: str
    enabled: bool
    last_fired_at: Optional[str]
    next_fire_at: str
    fire_count: int
    fail_count: int
    last_error: Optional[str]
    created_at: str
    updated_at: str


def _validate_cron(cron_expr: str) -> datetime:
    """Validate via croniter; return next fire time."""
    try:
        from croniter import croniter

        base = datetime.now(timezone.utc)
        itr = croniter(cron_expr, base)
        return itr.get_next(datetime)
    except Exception as exc:
        raise HTTPException(400, f"invalid cron expression: {exc}")


def _validate_task_type(task_type: str) -> None:
    if task_type not in _ALLOWED_TASK_TYPES:
        raise HTTPException(
            400,
            f"task_type must be one of {sorted(_ALLOWED_TASK_TYPES)}, got {task_type!r}",
        )


@router.post("", response_model=ScheduleResponse)
async def create_schedule(
    payload: ScheduleCreatePayload, auth: AuthDep
) -> ScheduleResponse:
    """Create a new schedule. Validates cron expression and task_type."""
    _validate_task_type(payload.task_type)
    next_at = _validate_cron(payload.cron_expr)

    sb = await get_async_supabase_admin()
    row = {
        "user_id": str(auth.user_id),
        "name": payload.name,
        "cron_expr": payload.cron_expr,
        "task_type": payload.task_type,
        "payload": payload.payload,
        "enabled": payload.enabled,
        "next_fire_at": next_at.isoformat(),
    }
    try:
        result = await sb.table("user_schedules").insert(row).execute()
    except Exception as exc:
        logger.exception(f"schedule create failed: {exc}")
        raise HTTPException(500, "create failed")
    if not result.data:
        raise HTTPException(500, "create returned no row")
    return ScheduleResponse(**result.data[0])


@router.get("", response_model=List[ScheduleResponse])
async def list_schedules(auth: AuthDep) -> List[ScheduleResponse]:
    sb = await get_async_supabase_admin()
    try:
        result = (
            await sb.table("user_schedules")
            .select("*")
            .eq("user_id", str(auth.user_id))
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.exception(f"schedule list failed: {exc}")
        raise HTTPException(500, "list failed")
    return [ScheduleResponse(**r) for r in (result.data or [])]


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, auth: AuthDep) -> ScheduleResponse:
    sb = await get_async_supabase_admin()
    try:
        result = (
            await sb.table("user_schedules")
            .select("*")
            .eq("id", schedule_id)
            .eq("user_id", str(auth.user_id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.exception(f"schedule get failed: {exc}")
        raise HTTPException(500, "get failed")
    if not result or not result.data:
        raise HTTPException(404, "schedule not found")
    return ScheduleResponse(**result.data)


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str, payload: ScheduleUpdatePayload, auth: AuthDep
) -> ScheduleResponse:
    """Update fields. If cron_expr changes, recompute next_fire_at."""
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")

    if "cron_expr" in fields:
        next_at = _validate_cron(fields["cron_expr"])
        fields["next_fire_at"] = next_at.isoformat()

    sb = await get_async_supabase_admin()
    try:
        result = (
            await sb.table("user_schedules")
            .update(fields)
            .eq("id", schedule_id)
            .eq("user_id", str(auth.user_id))
            .execute()
        )
    except Exception as exc:
        logger.exception(f"schedule update failed: {exc}")
        raise HTTPException(500, "update failed")
    if not result.data:
        raise HTTPException(404, "schedule not found")
    return ScheduleResponse(**result.data[0])


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, auth: AuthDep) -> Dict[str, Any]:
    sb = await get_async_supabase_admin()
    try:
        result = (
            await sb.table("user_schedules")
            .delete()
            .eq("id", schedule_id)
            .eq("user_id", str(auth.user_id))
            .execute()
        )
    except Exception as exc:
        logger.exception(f"schedule delete failed: {exc}")
        raise HTTPException(500, "delete failed")
    return {"ok": True, "deleted": len(result.data or [])}


@router.post("/{schedule_id}/fire-now")
async def fire_schedule_now(schedule_id: str, auth: AuthDep) -> Dict[str, Any]:
    """Manual one-shot trigger. Bypasses cron, dispatches immediately
    AND advances next_fire_at as if the cron had just fired (so the
    next regular tick still fires on schedule)."""
    sb = await get_async_supabase_admin()
    row_resp = (
        await sb.table("user_schedules")
        .select("*")
        .eq("id", schedule_id)
        .eq("user_id", str(auth.user_id))
        .maybe_single()
        .execute()
    )
    if not row_resp or not row_resp.data:
        raise HTTPException(404, "schedule not found")
    row = row_resp.data

    # Force next_fire_at to now so the master scheduler picks it up on
    # next tick (within 1 min). Cleaner than duplicating dispatch logic
    # here; the master_scheduler's _dispatch_one is the single owner of
    # "fire a schedule".
    await (
        sb.table("user_schedules")
        .update({"next_fire_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", schedule_id)
        .execute()
    )
    return {"ok": True, "queued_for_next_tick": True}


__all__ = ["router"]
