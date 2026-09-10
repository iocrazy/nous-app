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

# How far ahead a one-shot wake-up may be armed (30 days). Imported, not
# re-typed: the agent-facing tool states this ceiling in its own description,
# and two numbers would mean one path silently allowing what the other
# refuses. A year-out wake-up is far more likely to be a typo than an
# intention, and the row would sit enabled — invisible — until then.
from app.services.ai.tools.schedule_wakeup_tool import MAX_WAKEUP_HORIZON
from app.services.issues.issue_visibility import assert_issue_visible

# The engine decides what a schedule may be. Importing the set instead of
# re-typing it is what keeps the API from accepting a task_type the master
# scheduler then skips every minute in silence (and from rejecting one it can
# actually fire). tests/test_schedules_router_autopilot.py pins the equality
# in both directions.
from app.workflows.scheduled_master import SUPPORTED_TASK_TYPES as _ALLOWED_TASK_TYPES

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


def _bad_request(code: str, message: str) -> HTTPException:
    """A 400 whose reason the caller can BRANCH on.

    ``app/core/exceptions.py`` wraps every HTTPException into the
    ``ErrorResponse`` envelope and only carries ``exc.detail`` through to
    ``details`` when it is a dict — a string detail arrives at the browser as
    ``code: "http_400"`` and nothing else. The scheduling UI has to tell
    "pick another time" apart from "say something in the note", so every
    rejection here is a dict."""
    return HTTPException(400, {"code": code, "message": message})


_ROUTINE_DELIVERY_POLICIES = {"skip_if_active", "always"}


def _validate_agent_routine_payload(payload: Dict[str, Any]) -> None:
    """agent_routine schedules carry their config in payload jsonb:
    {agent_slug, prompt_md, delivery_policy?}. Validate at create/update so
    the master scheduler never has to guess at fire time."""
    slug = (payload.get("agent_slug") or "").strip()
    prompt = (payload.get("prompt_md") or "").strip()
    if not slug:
        raise _bad_request(
            "agent_slug_required", "agent_routine payload requires agent_slug"
        )
    if not prompt:
        raise _bad_request(
            "prompt_md_required", "agent_routine payload requires prompt_md"
        )
    policy = payload.get("delivery_policy") or "skip_if_active"
    if policy not in _ROUTINE_DELIVERY_POLICIES:
        raise _bad_request(
            "invalid_delivery_policy",
            f"delivery_policy must be one of {sorted(_ROUTINE_DELIVERY_POLICIES)}",
        )


class ScheduleCreatePayload(BaseModel):
    """Two shapes behind one endpoint.

    RECURRING (every task_type but ``issue_wakeup``): ``name`` + ``cron_expr``
    are required and ``next_fire_at`` is computed from them.
    ONE-SHOT (``issue_wakeup``, mig 461): no cron at all — ``fire_at`` IS the
    fire time, and the row disables itself once it has fired. Both fields are
    therefore Optional at the schema level and required by the branch that
    needs them, with a typed code the UI can act on."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    cron_expr: Optional[str] = Field(None, min_length=1, max_length=100)
    task_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    # IANA tz name the cron is interpreted in (default UTC — old behavior).
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    # One-shot only: an absolute, timezone-aware instant.
    fire_at: Optional[datetime] = None


class ScheduleUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    cron_expr: Optional[str] = Field(None, min_length=1, max_length=100)
    payload: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    timezone: Optional[str] = Field(None, min_length=1, max_length=64)


class ScheduleResponse(BaseModel):
    id: str
    user_id: Optional[str]
    name: str
    # NULL on a one-shot wake-up (mig 461). Typed ``str`` here used to make
    # the LIST endpoint 500 on the whole page because of one such row.
    cron_expr: Optional[str]
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
    # W2a autopilot hardening (mig 370)
    timezone: str
    consecutive_fails: int
    paused_at: Optional[str]
    pause_reason: Optional[str]
    skipped_count: int
    stale_after_minutes: int


def _validate_timezone(tz_name: str) -> Any:
    """Validate an IANA timezone name via ZoneInfo; 400 on unknown. Returns
    the resolved tzinfo so the caller can anchor the cron base to it."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        raise _bad_request("invalid_timezone", f"invalid timezone: {tz_name!r}")


def _validate_cron(cron_expr: str, tz_name: str = "UTC") -> datetime:
    """Validate the cron via croniter and return the next fire time as UTC,
    interpreting the cron in ``tz_name`` (so "0 9 * * *" means 9am local and
    survives DST). Mirrors scheduled_master._compute_next_fire."""
    tz = _validate_timezone(tz_name)
    try:
        from croniter import croniter

        base = datetime.now(tz)
        itr = croniter(cron_expr, base)
        return itr.get_next(datetime).astimezone(timezone.utc)
    except HTTPException:
        raise
    except Exception as exc:
        raise _bad_request("invalid_cron", f"invalid cron expression: {exc}")


def _validate_task_type(task_type: str) -> None:
    if task_type not in _ALLOWED_TASK_TYPES:
        raise _bad_request(
            "unsupported_task_type",
            f"task_type must be one of {sorted(_ALLOWED_TASK_TYPES)}, "
            f"got {task_type!r}",
        )


async def _validate_issue_wakeup_payload(
    payload: "ScheduleCreatePayload", body: Dict[str, Any], auth: Any
) -> str:
    """Validate a one-shot wake-up and return its text. Mutates ``body`` with
    the two defaults the CHECK constraint and the fire path rely on.

    The issue is resolved through the SHARED visibility rule, so arming a
    wake-up on someone else's issue 404s exactly like reading it would —
    existence never leaks."""
    fire_at = payload.fire_at
    if fire_at is None:
        raise _bad_request("fire_at_required", "issue_wakeup requires fire_at")
    if fire_at.tzinfo is None:
        raise _bad_request("fire_at_required", "fire_at must carry a timezone offset")
    now = datetime.now(timezone.utc)
    if not (now < fire_at <= now + MAX_WAKEUP_HORIZON):
        raise _bad_request(
            "fire_at_out_of_range",
            f"fire_at must be in the future and within "
            f"{MAX_WAKEUP_HORIZON.days} days",
        )

    text = str(body.get("text") or "").strip()
    if not text:
        raise _bad_request("text_required", "issue_wakeup payload requires text")

    issue_id = body.get("issue_id")
    if not issue_id:
        raise _bad_request(
            "issue_id_required", "issue_wakeup payload requires issue_id"
        )
    await assert_issue_visible(int(issue_id), auth)

    body["text"] = text
    body["issue_id"] = int(issue_id)
    # ``once`` is what the mig-461 CHECK reads to allow a NULL cron_expr; a row
    # without it would be rejected by the database, not by us.
    body.setdefault("once", True)
    body.setdefault("created_by", "user")
    return text


@router.post("", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    payload: ScheduleCreatePayload, auth: AuthDep
) -> ScheduleResponse:
    """Create a schedule — recurring (cron) or one-shot (issue wake-up)."""
    _validate_task_type(payload.task_type)
    body = dict(payload.payload)
    if payload.task_type == "issue_wakeup":
        text = await _validate_issue_wakeup_payload(payload, body, auth)
        name: Optional[str] = (payload.name or text)[:200]
        cron_expr: Optional[str] = None
        next_at = payload.fire_at
    else:
        if not payload.name or not payload.cron_expr:
            raise _bad_request(
                "cron_required", "name and cron_expr are required for this task_type"
            )
        name, cron_expr = payload.name, payload.cron_expr
        next_at = _validate_cron(payload.cron_expr, payload.timezone)
        if payload.task_type == "agent_routine":
            _validate_agent_routine_payload(body)

    row = {
        "user_id": str(auth.user_id),
        "name": name,
        "cron_expr": cron_expr,
        "task_type": payload.task_type,
        "payload": body,
        "enabled": payload.enabled,
        "timezone": payload.timezone,
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

    # We need the existing row's cron_expr / timezone / task_type to (a)
    # validate agent_routine payload edits and (b) recompute next_fire_at when
    # either cron_expr OR timezone changes (each depends on the other's
    # effective value). One read covers both.
    needs_existing = (
        ("payload" in fields) or ("cron_expr" in fields) or ("timezone" in fields)
    )
    existing_row: Optional[Mapping[str, Any]] = None
    if needs_existing:
        async with read_scope() as session:
            existing_row = (
                (
                    await session.execute(
                        select(
                            UserSchedules.task_type,
                            UserSchedules.cron_expr,
                            UserSchedules.timezone,
                        )
                        .where(UserSchedules.id == schedule_id)
                        .where(UserSchedules.user_id == str(auth.user_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

    # agent_routine payload edits must keep the contract the master
    # scheduler relies on. (task_type itself is immutable on update.)
    if (
        "payload" in fields
        and isinstance(fields["payload"], dict)
        and existing_row is not None
        and existing_row["task_type"] == "agent_routine"
    ):
        _validate_agent_routine_payload(fields["payload"])

    if "cron_expr" in fields or "timezone" in fields:
        effective_cron = fields.get("cron_expr") or (
            existing_row["cron_expr"] if existing_row else None
        )
        effective_tz = fields.get("timezone") or (
            existing_row["timezone"] if existing_row else "UTC"
        )
        # A one-shot has no cron to recompute from. The old fallback invented
        # "* * * * *" here, which would have re-armed it every minute forever;
        # its fire time is whatever fire_at set and stays put.
        if effective_cron:
            fields["next_fire_at"] = _validate_cron(effective_cron, effective_tz)

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


@router.post("/{schedule_id}/resume", response_model=ScheduleResponse)
async def resume_schedule(schedule_id: str, auth: AuthDep) -> ScheduleResponse:
    """Clear an auto-paused (or manually disabled) schedule: re-enable it,
    reset the consecutive-failure run, drop the pause metadata, and recompute
    next_fire_at from its cron + timezone so it fires fresh rather than
    immediately backfilling. Owner-only."""
    async with read_scope() as session:
        existing = (
            (
                await session.execute(
                    select(UserSchedules.cron_expr, UserSchedules.timezone)
                    .where(UserSchedules.id == schedule_id)
                    .where(UserSchedules.user_id == str(auth.user_id))
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if existing is None:
        raise HTTPException(404, "schedule not found")

    # A one-shot carries no cron: resuming it means "wake me now" (the master
    # picks it up on the next tick). Feeding None to croniter would 400 the
    # only way a user has to revive a wake-up that was disabled.
    next_at = (
        _validate_cron(existing["cron_expr"], existing["timezone"] or "UTC")
        if existing["cron_expr"]
        else datetime.now(timezone.utc)
    )
    try:
        async with write_scope() as session:
            rows = (
                (
                    await session.execute(
                        sa_update(UserSchedules)
                        .where(UserSchedules.id == schedule_id)
                        .where(UserSchedules.user_id == str(auth.user_id))
                        .values(
                            enabled=True,
                            consecutive_fails=0,
                            paused_at=None,
                            pause_reason=None,
                            last_error=None,
                            next_fire_at=next_at,
                        )
                        .returning(*UserSchedules.__table__.columns)
                    )
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        logger.exception(f"schedule resume failed: {exc}")
        raise HTTPException(500, "resume failed")
    if not rows:
        raise HTTPException(404, "schedule not found")
    return ScheduleResponse(**_serialize_schedule(rows[0]))


__all__ = ["router"]
