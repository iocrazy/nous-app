"""flows_router — Task Flow CRUD + cascade cancel.

A flow is a parent grouping for related tasks (typical: parse →
download → transcribe → summary all chained from one user URL
submission). The DB schema lives in mig 203 (`public.task_flows`);
the trigger `trg_task_tracking_flow_aggregate` keeps the parent
counters in sync with child phase transitions.

This router exposes:

  * ``POST   /api/v1/flows``            — create a flow
  * ``GET    /api/v1/flows``            — list user's flows
  * ``GET    /api/v1/flows/{id}``       — single flow with child tasks
  * ``POST   /api/v1/flows/{id}/cancel`` — cascade cancel children
  * ``DELETE /api/v1/flows/{id}``       — delete row (children FK SET NULL)

Tasks are attached by writing ``task_tracking.flow_id`` at creation
time (callers do this via the existing ``UnifiedTaskManager.create``
path with the new ``flow_id`` kwarg — wired in a follow-up). This PR
ships the flow surface + cascade cancel only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select
from sqlalchemy import update as sa_update

from app.core.deps import AuthDep
from app.db.session import read_scope, write_scope
from app.models import TaskFlows, TaskTracking
from app.services.infra.unified_task_manager import ACTIVE_PHASES_SQL

router = APIRouter(prefix="/flows", tags=["Task Flows"])


def _serialize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Coerce a task_flows / task_tracking row mapping to the JSON-safe
    primitives the PostgREST path returned (timestamptz → ISO-8601 str,
    uuid → str) so the pydantic response models validate."""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


class FlowCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    cascade_cancel: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FlowResponse(BaseModel):
    id: str
    user_id: str
    name: str
    state: str
    cascade_cancel: bool
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class FlowDetailResponse(FlowResponse):
    tasks: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("", response_model=FlowResponse)
async def create_flow(payload: FlowCreatePayload, auth: AuthDep) -> FlowResponse:
    """Create a new flow row. Children are added by writing
    ``task_tracking.flow_id`` at task creation time."""
    try:
        async with write_scope() as session:
            created = (
                (
                    await session.execute(
                        insert(TaskFlows)
                        .values(
                            user_id=str(auth.user_id),
                            name=payload.name,
                            cascade_cancel=payload.cascade_cancel,
                            metadata_=payload.metadata,
                        )
                        .returning(*TaskFlows.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        logger.exception(f"flow create failed: {exc}")
        raise HTTPException(500, "create failed")
    if not created:
        raise HTTPException(500, "create returned no row")
    return FlowResponse(**_serialize_row(created))


@router.get("", response_model=List[FlowResponse])
async def list_flows(
    auth: AuthDep,
    state: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> List[FlowResponse]:
    """List the caller's flows. Optionally filter by state."""
    stmt = select(*TaskFlows.__table__.columns).where(
        TaskFlows.user_id == str(auth.user_id)
    )
    if state:
        stmt = stmt.where(TaskFlows.state == state)
    stmt = stmt.order_by(TaskFlows.created_at.desc()).offset(offset).limit(limit)
    try:
        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()
    except Exception as exc:
        logger.exception(f"flow list failed: {exc}")
        raise HTTPException(500, "list failed")
    return [FlowResponse(**_serialize_row(r)) for r in rows]


@router.get("/{flow_id}", response_model=FlowDetailResponse)
async def get_flow(flow_id: str, auth: AuthDep) -> FlowDetailResponse:
    """Return the flow + its child tasks (joined inline)."""
    try:
        async with read_scope() as session:
            flow = (
                (
                    await session.execute(
                        select(*TaskFlows.__table__.columns)
                        .where(TaskFlows.id == flow_id)
                        .where(TaskFlows.user_id == str(auth.user_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        logger.exception(f"flow get failed: {exc}")
        raise HTTPException(500, "get failed")
    if not flow:
        raise HTTPException(404, "flow not found")

    async with read_scope() as session:
        tasks = (
            (
                await session.execute(
                    select(*TaskTracking.__table__.columns)
                    .where(TaskTracking.flow_id == flow_id)
                    .order_by(TaskTracking.created_at.asc())
                )
            )
            .mappings()
            .all()
        )
    return FlowDetailResponse(
        **_serialize_row(flow),
        tasks=[_serialize_row(t) for t in tasks],
    )


@router.post("/{flow_id}/cancel")
async def cancel_flow(flow_id: str, auth: AuthDep) -> Dict[str, Any]:
    """Cascade-cancel: set flow state=cancelled + signal abort on every
    non-terminal child task. The trigger will eventually update the
    aggregate counters as children flip to cancelled.

    Implementation:
      1. Mark flow.state = cancelled (this prevents the trigger from
         regressing to running on next child phase change)
      2. Read non-terminal children
      3. For each, signal abort via the AbortRegistry (cross-process via
         lifecycle bus per A10) AND mark its task_tracking row cancelled
         so UI reflects immediately
    """
    # Verify ownership + read flow.
    try:
        async with read_scope() as session:
            flow = (
                (
                    await session.execute(
                        select(
                            TaskFlows.id,
                            TaskFlows.state,
                            TaskFlows.cascade_cancel,
                        )
                        .where(TaskFlows.id == flow_id)
                        .where(TaskFlows.user_id == str(auth.user_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        logger.exception(f"flow cancel lookup failed: {exc}")
        raise HTTPException(500, "lookup failed")
    if not flow:
        raise HTTPException(404, "flow not found")
    if flow["state"] in ("completed", "failed", "cancelled", "partial"):
        return {"ok": True, "noop": True, "reason": f"already {flow['state']}"}

    # Mark the flow cancelled first so the aggregate trigger doesn't flip
    # back to running between us reading children and them transitioning.
    async with write_scope() as session:
        await session.execute(
            sa_update(TaskFlows)
            .where(TaskFlows.id == flow_id)
            .values(state="cancelled")
        )

    if not flow.get("cascade_cancel", True):
        return {"ok": True, "cascaded": 0, "reason": "cascade_cancel=false"}

    # Find non-terminal children. ACTIVE_PHASES_SQL, never a hand-written list:
    # this filter used to be ("queued", "in_progress") and therefore matched no
    # running child at all (a live workflow task sits at 'processing' — see the
    # two-writers note in unified_task_manager), so cascade_cancel silently
    # cancelled the flow row while every child kept running.
    async with read_scope() as session:
        children = (
            (
                await session.execute(
                    select(TaskTracking.dbos_workflow_id, TaskTracking.phase)
                    .where(TaskTracking.flow_id == flow_id)
                    .where(TaskTracking.phase.in_(ACTIVE_PHASES_SQL))
                )
            )
            .mappings()
            .all()
        )

    # Signal abort + mark cancelled.
    from app.services.abort_registry import get_registry

    registry = get_registry()
    cancelled = 0
    for child in children:
        wf_id = child.get("dbos_workflow_id")
        if not wf_id:
            continue
        try:
            await registry.signal(wf_id, broadcast=True)
            async with write_scope() as session:
                await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.dbos_workflow_id == wf_id)
                    .values(
                        phase="cancelled",
                        status="cancelled",
                        error_code="flow_cascade_cancel",
                        error_msg=f"flow {flow_id} cancelled",
                    )
                )
            cancelled += 1
        except Exception as exc:
            logger.opt(exception=True).warning(
                f"flow cascade cancel for child {wf_id} failed: {exc}"
            )

    return {"ok": True, "cascaded": cancelled}


@router.delete("/{flow_id}")
async def delete_flow(flow_id: str, auth: AuthDep) -> Dict[str, Any]:
    """Hard delete a flow row. Child task_tracking rows survive (FK is
    ON DELETE SET NULL) so historical task records aren't lost."""
    try:
        async with write_scope() as session:
            deleted = (
                await session.execute(
                    sa_delete(TaskFlows)
                    .where(TaskFlows.id == flow_id)
                    .where(TaskFlows.user_id == str(auth.user_id))
                    .returning(TaskFlows.id)
                )
            ).all()
    except Exception as exc:
        logger.exception(f"flow delete failed: {exc}")
        raise HTTPException(500, "delete failed")
    return {"ok": True, "deleted": len(deleted)}


__all__ = ["router"]
