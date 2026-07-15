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
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select
from sqlalchemy import update as sa_update

from app.core.deps import AuthDep
from app.db.session import read_scope, write_scope
from app.models import UserSchedules

router = APIRouter(prefix="/schedules", tags=["Schedules"])


def _serialize_schedule(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Coerce a user_schedules row mapping to the JSON-safe primitives the
    PostgREST path returned (timestamptz → ISO-8601 str, uuid → str) so
    ``ScheduleResponse`` (which types ``id`` / timestamps as str) validates."""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


_ALLOWED_TASK_TYPES = {
    "parse",
    "download",
    "transcode",
    "ai_summary",
    "ai_transcription",
    "ai_visual_analysis",
    # paperclip R1: routine fires create an issue assigned to an agent
    # (origin_kind='routine') and dispatch execute_issue — see
    # scheduled_master._fire_agent_routine. Payload contract validated in
    # _validate_agent_routine_payload.
    "agent_routine",
}

_ROUTINE_DELIVERY_POLICIES = {"skip_if_active", "always"}


def _validate_agent_routine_payload(payload: Dict[str, Any]) -> None:
    """agent_routine schedules carry their config in payload jsonb:
    {agent_slug, prompt_md, delivery_policy?}. Validate at create/update so
    the master scheduler never has to guess at fire time."""
    slug = (payload.get("agent_slug") or "").strip()
    prompt = (payload.get("prompt_md") or "").strip()
    if not slug:
        raise HTTPException(400, "agent_routine payload requires agent_slug")
    if not prompt:
        raise HTTPException(400, "agent_routine payload requires prompt_md")
    policy = payload.get("delivery_policy") or "skip_if_active"
    if policy not in _ROUTINE_DELIVERY_POLICIES:
        raise HTTPException(
            400,
            f"delivery_policy must be one of {sorted(_ROUTINE_DELIVERY_POLICIES)}",
        )


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
    if payload.task_type == "agent_routine":
        _validate_agent_routine_payload(payload.payload)

    row = {
        "user_id": str(auth.user_id),
        "name": payload.name,
        "cron_expr": payload.cron_expr,
        "task_type": payload.task_type,
        "payload": payload.payload,
        "enabled": payload.enabled,
        "next_fire_at": next_at,
    }
    try:
        async with write_scope() as session:
            created = (
                (
                    await session.execute(
                        insert(UserSchedules)
                        .values(**row)
                        .returning(*UserSchedules.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        logger.exception(f"schedule create failed: {exc}")
        raise HTTPException(500, "create failed")
    if not created:
        raise HTTPException(500, "create returned no row")
    return ScheduleResponse(**_serialize_schedule(created))


@router.get("", response_model=List[ScheduleResponse])
async def list_schedules(auth: AuthDep) -> List[ScheduleResponse]:
    try:
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(*UserSchedules.__table__.columns)
                        .where(UserSchedules.user_id == str(auth.user_id))
                        .order_by(UserSchedules.created_at.desc())
                    )
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        logger.exception(f"schedule list failed: {exc}")
        raise HTTPException(500, "list failed")
    return [ScheduleResponse(**_serialize_schedule(r)) for r in rows]


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, auth: AuthDep) -> ScheduleResponse:
    try:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*UserSchedules.__table__.columns)
                        .where(UserSchedules.id == schedule_id)
                        .where(UserSchedules.user_id == str(auth.user_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        logger.exception(f"schedule get failed: {exc}")
        raise HTTPException(500, "get failed")
    if not row:
        raise HTTPException(404, "schedule not found")
    return ScheduleResponse(**_serialize_schedule(row))


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str, payload: ScheduleUpdatePayload, auth: AuthDep
) -> ScheduleResponse:
    """Update fields. If cron_expr changes, recompute next_fire_at."""
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")

    # agent_routine payload edits must keep the contract the master
    # scheduler relies on. (task_type itself is immutable on update.)
    if "payload" in fields and isinstance(fields["payload"], dict):
        async with read_scope() as session:
            existing = (
                await session.execute(
                    select(UserSchedules.task_type)
                    .where(UserSchedules.id == schedule_id)
                    .where(UserSchedules.user_id == str(auth.user_id))
                    .limit(1)
                )
            ).first()
        if existing is not None and existing[0] == "agent_routine":
            _validate_agent_routine_payload(fields["payload"])

    if "cron_expr" in fields:
        next_at = _validate_cron(fields["cron_expr"])
        fields["next_fire_at"] = next_at

    try:
        async with write_scope() as session:
            rows = (
                (
                    await session.execute(
                        sa_update(UserSchedules)
                        .where(UserSchedules.id == schedule_id)
                        .where(UserSchedules.user_id == str(auth.user_id))
                        .values(**fields)
                        .returning(*UserSchedules.__table__.columns)
                    )
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        logger.exception(f"schedule update failed: {exc}")
        raise HTTPException(500, "update failed")
    if not rows:
        raise HTTPException(404, "schedule not found")
    return ScheduleResponse(**_serialize_schedule(rows[0]))


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, auth: AuthDep) -> Dict[str, Any]:
    try:
        async with write_scope() as session:
            deleted = (
                await session.execute(
                    sa_delete(UserSchedules)
                    .where(UserSchedules.id == schedule_id)
                    .where(UserSchedules.user_id == str(auth.user_id))
                    .returning(UserSchedules.id)
                )
            ).all()
    except Exception as exc:
        logger.exception(f"schedule delete failed: {exc}")
        raise HTTPException(500, "delete failed")
    return {"ok": True, "deleted": len(deleted)}


@router.post("/{schedule_id}/fire-now")
async def fire_schedule_now(schedule_id: str, auth: AuthDep) -> Dict[str, Any]:
    """Manual one-shot trigger. Bypasses cron, dispatches immediately
    AND advances next_fire_at as if the cron had just fired (so the
    next regular tick still fires on schedule)."""
    async with read_scope() as session:
        row = (
            await session.execute(
                select(UserSchedules.id)
                .where(UserSchedules.id == schedule_id)
                .where(UserSchedules.user_id == str(auth.user_id))
                .limit(1)
            )
        ).first()
    if row is None:
        raise HTTPException(404, "schedule not found")

    # Force next_fire_at to now so the master scheduler picks it up on
    # next tick (within 1 min). Cleaner than duplicating dispatch logic
    # here; the master_scheduler's _dispatch_one is the single owner of
    # "fire a schedule".
    async with write_scope() as session:
        await session.execute(
            sa_update(UserSchedules)
            .where(UserSchedules.id == schedule_id)
            .values(next_fire_at=datetime.now(timezone.utc))
        )
    return {"ok": True, "queued_for_next_tick": True}


__all__ = ["router"]
