"""Canvas REST endpoints (Phase 1 of canvas + AI upgrade).

Surface area:
  GET    /api/v1/canvases/{canvas_id}                    — load
  PUT    /api/v1/canvases/{canvas_id}                    — save w/ optimistic lock
  DELETE /api/v1/canvases/{canvas_id}                    — remove
  GET    /api/v1/projects/{project_id}/canvases          — list within project
  POST   /api/v1/projects/{project_id}/canvases          — create within project

Project-membership gating piggy-backs on the existing
``verify_project_*_access`` guards from ``app.core.scope_guards`` for the
project-scoped routes. The canvas-scoped routes resolve the parent
project via the repo and then call the same guard.
"""

from __future__ import annotations

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
from app.services.canvas import CanvasConflict, CanvasService

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
