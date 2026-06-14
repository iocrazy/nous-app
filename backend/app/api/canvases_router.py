"""Canvas REST endpoints (Phase 1 of canvas + AI upgrade).

Surface area:
  GET    /api/v1/canvases/{canvas_id}                    — load
  PUT    /api/v1/canvases/{canvas_id}                    — save w/ optimistic lock
  DELETE /api/v1/canvases/{canvas_id}                    — remove
  GET    /api/v1/projects/{project_id}/canvases          — list within project
  POST   /api/v1/projects/{project_id}/canvases          — create within project
  POST   /api/v1/canvases/{canvas_id}/graph-runs         — enqueue full-graph run (Phase 6d)

Project-membership gating piggy-backs on the existing
``verify_project_*_access`` guards from ``app.core.scope_guards`` for the
project-scoped routes. The canvas-scoped routes resolve the parent
project via the repo and then call the same guard.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Path
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_project_write_access
from app.schemas.canvas import (
    CanvasConflictResponse,
    CanvasCreate,
    CanvasResponse,
    CanvasUpdate,
)
from app.schemas.canvas_run import (
    CanvasGraphRunRequest,
    CanvasGraphRunResponse,
    CanvasPromptRunRequest,
    CanvasPromptRunResponse,
    ClassicNodeRunRequest,
    ClassicNodeRunResponse,
)
from app.services.canvas import CanvasConflict, CanvasService
from app.services.canvas.canvas_run_service import CanvasRunService

router = APIRouter()


def _to_response(row: dict) -> dict:
    """Normalise raw DB row → CanvasResponse-shaped dict.

    Supabase returns BIGINT IDs as JSON numbers; we stringify so the
    frontend doesn't lose snowflake precision (the bigIntSafeFetch
    wrapper is for raw client fetches — going through FastAPI we hand
    the stringification ourselves).
    """
    if not row:
        return row
    out = dict(row)
    if "id" in out and out["id"] is not None:
        out["id"] = str(out["id"])
    if "project_id" in out and out["project_id"] is not None:
        out["project_id"] = str(out["project_id"])
    if "created_by" in out and out["created_by"] is not None:
        out["created_by"] = str(out["created_by"])
    return out


async def _gate_canvas_write(canvas_id: str, auth: AuthDep) -> str:
    """Resolve canvas → project, then run the write guard. Returns project_id."""
    svc = CanvasService()
    project_id = await svc.get_project_id(canvas_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    await verify_project_write_access(project_id=project_id, auth=auth)
    return project_id


async def _gate_canvas_read(canvas_id: str, auth: AuthDep) -> str:
    # Read access = project membership = same guard as write today;
    # split into a separate verify_project_read_access if mediahub grows
    # a viewer-only role later.
    return await _gate_canvas_write(canvas_id, auth)


# ============================================================
# Canvas-scoped routes
# ============================================================


@router.get("/canvases/{canvas_id}")
async def get_canvas(
    auth: AuthDep,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    await _gate_canvas_read(canvas_id, auth)
    svc = CanvasService()
    row = await svc.get(canvas_id)
    if row is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    return {"success": True, "data": _to_response(row)}


@router.put("/canvases/{canvas_id}")
async def update_canvas(
    auth: AuthDep,
    payload: CanvasUpdate,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    await _gate_canvas_write(canvas_id, auth)
    svc = CanvasService()
    try:
        row = await svc.update_with_lock(canvas_id, payload)
        return {"success": True, "data": _to_response(row)}
    except LookupError:
        raise HTTPException(status_code=404, detail="canvas not found")
    except CanvasConflict as conflict:
        body = CanvasConflictResponse(
            current=CanvasResponse(**_to_response(conflict.current))
        )
        raise HTTPException(status_code=409, detail=body.model_dump(mode="json"))
    except Exception as exc:  # pragma: no cover - belt-and-braces
        logger.exception(f"canvas PUT {canvas_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="canvas save failed")


@router.delete("/canvases/{canvas_id}")
async def delete_canvas(
    auth: AuthDep,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    await _gate_canvas_write(canvas_id, auth)
    svc = CanvasService()
    ok = await svc.delete(canvas_id)
    if not ok:
        raise HTTPException(status_code=404, detail="canvas not found")
    return {"success": True}


# ============================================================
# Project-scoped routes
# ============================================================


@router.get("/projects/{project_id}/canvases")
async def list_project_canvases(
    auth: AuthDep,
    project_id: str = Path(..., description="Snowflake project ID"),
) -> dict:
    await verify_project_write_access(project_id=project_id, auth=auth)
    svc = CanvasService()
    rows = await svc.list_for_project(project_id)
    return {"success": True, "data": [_to_response(r) for r in rows]}


@router.post("/projects/{project_id}/canvases")
async def create_project_canvas(
    auth: AuthDep,
    payload: CanvasCreate,
    project_id: str = Path(..., description="Snowflake project ID"),
) -> dict:
    await verify_project_write_access(project_id=project_id, auth=auth)
    svc = CanvasService()
    row = await svc.create_in_project(project_id, payload, created_by=auth.user_id)
    if row is None:
        raise HTTPException(status_code=500, detail="canvas create failed")
    return {"success": True, "data": _to_response(row)}


# ============================================================
# Smart-mode prompt run
# ============================================================


@router.post("/canvases/runs/prompts")
async def run_canvas_prompt(
    auth: AuthDep,
    payload: CanvasPromptRunRequest,
) -> dict:
    """Execute one smart-canvas prompt run.

    Failure modes (adapter init / LLM call / empty body) are returned
    in-band via {ok: false, error}; the HTTP layer always returns 200
    unless gating fails. The frontend's `backendRunner` distinguishes
    ok vs failed by reading the body, not the status code.
    """
    await _gate_canvas_write(payload.canvas_id, auth)
    svc = CanvasRunService()
    result = await svc.run_prompt(
        body=payload.body,
        provider_slug=payload.provider_slug,
        agent_id=payload.agent_id,
    )
    body = CanvasPromptRunResponse(ok=result.ok, text=result.text, error=result.error)
    return {"success": True, "data": body.model_dump(mode="json")}


@router.post("/canvases/runs/classic-node")
async def run_classic_node(
    auth: AuthDep,
    payload: ClassicNodeRunRequest,
) -> dict:
    """Execute one ClassicMode node run (server-resolved route).

    The cascade hands the node here instead of guessing a provider_slug
    client-side. The run service resolves the route (llm/comfy provider,
    image_gen op, or in-band reject) and runs it synchronously. Failure
    modes are returned in-band via {ok: false, error}; HTTP stays 200 unless
    gating fails. ``result`` carries structured op output (image_url for
    image_gen).
    """
    project_id = await _gate_canvas_write(payload.canvas_id, auth)
    svc = CanvasRunService()
    result = await svc.run_classic_node(
        node_type=payload.node.type,
        node={"data": payload.node.data},
        body=payload.body,
        agent_id=payload.agent_id,
        node_id=payload.node.id,
        project_id=project_id,
    )
    body = ClassicNodeRunResponse(
        ok=result.ok,
        text=result.text,
        error=result.error,
        result=result.result,
    )
    return {"success": True, "data": body.model_dump(mode="json")}


# ============================================================
# Full-graph canvas run (Phase 6d M1)
# ============================================================


@router.post("/canvases/{canvas_id}/graph-runs")
async def enqueue_canvas_graph_run(
    auth: AuthDep,
    payload: CanvasGraphRunRequest,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Enqueue a full-graph canvas run as a DBOS workflow.

    Returns immediately with the ``task_id`` / ``dbos_workflow_id`` — the
    workflow executes asynchronously. Poll ``GET /api/v1/workflows/{id}`` or
    listen to the TaskManagerContext Realtime channel for status updates.

    路线 C id-match contract: ``manager.create(dbos_workflow_id=wf_id)`` and
    ``start_workflow_routed(workflow_id=wf_id)`` receive the SAME ``wf_id``
    so the task_tracking row and the DBOS workflow are always linked.
    """
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.canvas_graph import canvas_graph_workflow

    await _gate_canvas_write(canvas_id, auth)

    wf_id = str(uuid.uuid4())

    # Create task_tracking row first (id-match: same wf_id passed below).
    try:
        await get_task_manager().create(
            user_id=auth.user_id,
            task_type="canvas_graph_run",
            title=f"Graph Run {canvas_id[:16]}",
            dbos_workflow_id=wf_id,
            metadata={
                "canvas_id": canvas_id,
                "node_count": len(payload.node_order),
                "continue_on_failure": payload.continue_on_failure,
            },
        )
    except Exception as exc:
        logger.warning(
            f"[canvases.graph_run] pre-create task_tracking row failed: {exc!r}"
        )

    # Enqueue DBOS workflow (id-match: workflow_id=wf_id, same as above).
    try:
        await start_workflow_routed(
            "canvas_graph_run",
            dbos_workflow_callable=canvas_graph_workflow,
            dbos_workflow_kwargs={
                "canvas_id": canvas_id,
                "node_order": payload.node_order,
                "user_id": auth.user_id,
                "continue_on_failure": payload.continue_on_failure,
            },
            workflow_id=wf_id,
        )
    except Exception as exc:
        logger.exception(f"[canvases.graph_run] enqueue failed: {exc!r}")
        raise HTTPException(
            status_code=500,
            detail=f"canvas graph run enqueue failed: {exc}",
        )

    body = CanvasGraphRunResponse(task_id=wf_id, dbos_workflow_id=wf_id)
    return {"success": True, "data": body.model_dump(mode="json")}


@router.get("/canvases/providers/nous-center/verify")
async def verify_nous_center_protocol(auth: AuthDep) -> dict:
    """Confirm the nous-center service is reachable + the contract holds.

    Drives the "Verify protocol" button in the AI settings UI. Result
    is always in-band: {ok, base_url?, workflows_visible? | error}.
    Any authenticated user can probe (the call is read-only and uses
    the server-side service token, not the user's identity).
    """
    from app.core.config import get_settings
    from app.services.canvas.nous_center_verify import verify_nous_center

    # Touch auth so the dep injection is exercised — the value isn't
    # used, but the route still requires a logged-in caller.
    _ = auth.user_id

    settings = get_settings()
    result = await verify_nous_center(settings)
    return {"success": True, "data": result}
