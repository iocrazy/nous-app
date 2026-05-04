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

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin


router = APIRouter(prefix="/flows", tags=["Task Flows"])


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
    sb = await get_async_supabase_admin()
    row = {
        "user_id": str(auth.user_id),
        "name": payload.name,
        "cascade_cancel": payload.cascade_cancel,
        "metadata": payload.metadata,
    }
    try:
        result = await sb.table("task_flows").insert(row).execute()
    except Exception as exc:
        logger.exception(f"flow create failed: {exc}")
        raise HTTPException(500, "create failed")
    if not result.data:
        raise HTTPException(500, "create returned no row")
    return FlowResponse(**result.data[0])


@router.get("", response_model=List[FlowResponse])
async def list_flows(
    auth: AuthDep,
    state: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> List[FlowResponse]:
    """List the caller's flows. Optionally filter by state."""
    sb = await get_async_supabase_admin()
    q = (
        sb.table("task_flows")
        .select("*")
        .eq("user_id", str(auth.user_id))
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if state:
        q = q.eq("state", state)
    try:
        result = await q.execute()
    except Exception as exc:
        logger.exception(f"flow list failed: {exc}")
        raise HTTPException(500, "list failed")
    return [FlowResponse(**r) for r in (result.data or [])]


@router.get("/{flow_id}", response_model=FlowDetailResponse)
async def get_flow(flow_id: str, auth: AuthDep) -> FlowDetailResponse:
    """Return the flow + its child tasks (joined inline)."""
    sb = await get_async_supabase_admin()
    try:
        flow_result = (
            await sb.table("task_flows")
            .select("*")
            .eq("id", flow_id)
            .eq("user_id", str(auth.user_id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.exception(f"flow get failed: {exc}")
        raise HTTPException(500, "get failed")
    if not flow_result or not flow_result.data:
        raise HTTPException(404, "flow not found")

    tasks_result = (
        await sb.table("task_tracking")
        .select("*")
        .eq("flow_id", flow_id)
        .order("created_at", desc=False)
        .execute()
    )
    return FlowDetailResponse(
        **flow_result.data,
        tasks=tasks_result.data or [],
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
    sb = await get_async_supabase_admin()

    # Verify ownership + read flow.
    try:
        flow_result = (
            await sb.table("task_flows")
            .select("id, state, cascade_cancel")
            .eq("id", flow_id)
            .eq("user_id", str(auth.user_id))
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.exception(f"flow cancel lookup failed: {exc}")
        raise HTTPException(500, "lookup failed")
    if not flow_result or not flow_result.data:
        raise HTTPException(404, "flow not found")
    flow = flow_result.data
    if flow["state"] in ("completed", "failed", "cancelled", "partial"):
        return {"ok": True, "noop": True, "reason": f"already {flow['state']}"}

    # Mark the flow cancelled first so the aggregate trigger doesn't flip
    # back to running between us reading children and them transitioning.
    await (
        sb.table("task_flows")
        .update({"state": "cancelled"})
        .eq("id", flow_id)
        .execute()
    )

    if not flow.get("cascade_cancel", True):
        return {"ok": True, "cascaded": 0, "reason": "cascade_cancel=false"}

    # Find non-terminal children.
    children = (
        await sb.table("task_tracking")
        .select("dbos_workflow_id, phase")
        .eq("flow_id", flow_id)
        .in_("phase", ["queued", "in_progress"])
        .execute()
    ).data or []

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
            await (
                sb.table("task_tracking")
                .update(
                    {
                        "phase": "cancelled",
                        "status": "cancelled",
                        "error_code": "flow_cascade_cancel",
                        "error_msg": f"flow {flow_id} cancelled",
                    }
                )
                .eq("dbos_workflow_id", wf_id)
                .execute()
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
    sb = await get_async_supabase_admin()
    try:
        result = (
            await sb.table("task_flows")
            .delete()
            .eq("id", flow_id)
            .eq("user_id", str(auth.user_id))
            .execute()
        )
    except Exception as exc:
        logger.exception(f"flow delete failed: {exc}")
        raise HTTPException(500, "delete failed")
    return {"ok": True, "deleted": len(result.data or [])}


__all__ = ["router"]
