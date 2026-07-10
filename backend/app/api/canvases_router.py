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
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)
from app.repositories.canvas_repository import CanvasRepository
from app.schemas.canvas import (
    CanvasConflictResponse,
    CanvasCreate,
    CanvasGenerationRequest,
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
from app.services.infra.unified_task_manager import get_task_manager

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
    """Resolve canvas → project, then run the read guard. Returns project_id.

    Read = owner, or team member, or any project_members row (any role) —
    see ``verify_project_read_access``. Viewer-role project members can
    reach this but fail ``_gate_canvas_write``, which requires
    manager/editor.
    """
    svc = CanvasService()
    project_id = await svc.get_project_id(canvas_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    await verify_project_read_access(project_id=project_id, auth=auth)
    return project_id


# ============================================================
# Smart-canvas generation (G4-B1) — static paths MUST register before the
# dynamic /canvases/{canvas_id} below or they get captured as a canvas id.
# ============================================================

_GENERATION_MODEL_PUBLIC_FIELDS = (
    "name",
    "display_name",
    "type",
    "actual_provider",
    "sort_order",
)


@router.get("/canvases/generation-models")
async def list_generation_models(auth: AuthDep) -> dict:
    """Image/video rows from the mediahub_models catalog (public columns
    only — no api_key/base_url) for the composer's model picker."""
    from app.repositories import mediahub_model_repository as _repo_mod

    rows = await _repo_mod.get_mediahub_model_repository().list_enabled()
    data = [
        {k: r.get(k) for k in _GENERATION_MODEL_PUBLIC_FIELDS}
        for r in rows
        if r.get("type") in ("image", "video")
    ]
    return {"success": True, "data": data}


@router.get("/canvases/generations/{task_id}")
async def get_canvas_generation(task_id: str, auth: AuthDep) -> dict:
    """Poll one generation task. Reads task_tracking (the UI's single source
    of truth — route C); the durable result lands in metadata.result_url."""
    client = await get_task_manager()._get_client()
    result = await (
        client.table("task_tracking")
        .select("dbos_workflow_id, phase, status, error_msg, metadata")
        .eq("dbos_workflow_id", task_id)
        .eq("user_id", auth.user_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "data": result.data}


async def _is_team_member(team_id: str, user_id: str) -> bool:
    """Same membership check as scope_guards.verify_scope_access."""
    from app.db import get_async_supabase_admin

    client = await get_async_supabase_admin()
    result = (
        await client.table("team_members")
        .select("team_id")
        .eq("team_id", team_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return bool(result.data)


@router.get("/canvases/team/{team_id}")
async def list_team_canvases(team_id: str, auth: AuthDep) -> dict:
    """Every project in the team with its canvases embedded as summary
    columns — one query, replacing the canvas landing page's
    fetchProjects + per-project listCanvases N+1 fan-out."""
    if not await _is_team_member(team_id, auth.user_id):
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    rows = await CanvasRepository().list_team_tree(team_id)
    data = [
        {
            "project_id": str(r.get("id")),
            "project_name": r.get("name") or "",
            "canvases": [
                {
                    "id": str(c.get("id")),
                    "name": c.get("name") or "",
                    "kind": c.get("kind") or "smart",
                    "updated_at": c.get("updated_at"),
                }
                for c in (r.get("canvases") or [])
            ],
        }
        for r in rows
    ]
    return {"success": True, "data": data}


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


@router.post("/canvases/{canvas_id}/generations")
async def dispatch_canvas_generations(
    auth: AuthDep,
    payload: CanvasGenerationRequest,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Dispatch ``count``× image/video generation tasks for one node.

    Each item is an independent DBOS workflow with its own task_tracking row
    (the queue is the concurrency governor — the frontend never opens its own
    parallelism). ``count`` clamps to 8 (Infinite's cap); video always 1.
    Results arrive via GET /canvases/generations/{task_id} (metadata.result_url).
    """
    await _gate_canvas_write(canvas_id, auth)
    count = 1 if payload.kind == "video" else max(1, min(payload.count, 8))
    canvas_id_int = int(canvas_id) if canvas_id.isdigit() else None

    from app.services.infra import dbos_orchestrator
    from app.workflows.canvas_generation import canvas_generation_workflow

    mgr = get_task_manager()
    task_ids: list[str] = []
    for index in range(count):
        wf_id = str(uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="canvas_gen",  # ≤20 chars (task_tracking.task_type VARCHAR(20))
            title=f"Generate {payload.kind}",
            subtitle=payload.prompt[:80],
            dbos_workflow_id=wf_id,
            metadata={
                "canvas_id": canvas_id,
                "node_id": payload.node_id,
                "kind": payload.kind,
                "index": index + 1,
                "count": count,
            },
        )
        await dbos_orchestrator.start_workflow_routed(
            "canvas_generation",
            dbos_workflow_callable=canvas_generation_workflow,
            dbos_workflow_kwargs={
                "kind": payload.kind,
                "prompt": payload.prompt,
                "model": payload.model,
                "params": payload.params,
                "canvas_id": canvas_id_int,
                "node_id": payload.node_id,
                "user_id": auth.user_id,
                "source_url": payload.source_url,
            },
            workflow_id=wf_id,
        )
        task_ids.append(task_id)
    return {"success": True, "task_ids": task_ids}


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
        user_id=str(auth.user_id),
        canvas_id=int(payload.canvas_id),
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
