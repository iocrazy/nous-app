# backend/app/api/project_assets_router.py
"""Project Assets read endpoints (IC-port P4).

  GET /canvases/{id}/assets          — resources referenced by a canvas
  GET /resources/{id}/canvas-refs    — canvases that reference a resource
  GET /resources/project-assets/tree — project→canvas tree w/ asset counts

All reads JOIN with the caller's membership/ownership filter so the
RLS-locked refs table never leaks cross-tenant rows.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.api.media_permissions import check_media_access
from app.core.deps import AuthDep
from app.core.scope_guards import verify_project_write_access
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.repositories.projects_repository import ProjectsRepository
from app.services.canvas import CanvasService

router = APIRouter()


async def _gate_canvas_read(canvas_id: str, auth: AuthDep) -> str:
    """Resolve canvas → project, then require project membership.
    Mirrors canvases_router._gate_canvas_read."""
    svc = CanvasService()
    project_id = await svc.get_project_id(canvas_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    await verify_project_write_access(project_id=project_id, auth=auth)
    return project_id


@router.get("/canvases/{canvas_id}/assets")
async def canvas_assets(canvas_id: str, auth: AuthDep):
    await _gate_canvas_read(canvas_id, auth)
    repo = CanvasRefsRepository()
    items = await repo.list_assets_for_canvas(canvas_id)
    return {"success": True, "data": items}
