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


@router.get("/resources/project-assets/tree")
async def project_assets_tree(auth: AuthDep):
    """Project → Canvas tree (with per-canvas asset counts) for every
    project the caller can see. Projects with no canvases are included
    with an empty ``canvases`` list so the UI can show them as empty
    groups."""
    projects = await ProjectsRepository().get_user_projects(auth.user_id)
    project_ids = [str(p["id"]) for p in projects]
    rows = await CanvasRefsRepository().tree_for_projects(project_ids)

    by_project: dict[str, list] = {}
    for row in rows:
        by_project.setdefault(row["project_id"], []).append(
            {
                "canvas_id": row["canvas_id"],
                "canvas_name": row["canvas_name"],
                "kind": row["kind"],
                "asset_count": int(row.get("asset_count") or 0),
            }
        )

    data = [
        {
            "project_id": str(p["id"]),
            "name": p["name"],
            "canvases": by_project.get(str(p["id"]), []),
        }
        for p in projects
    ]
    return {"success": True, "data": data}


@router.get("/resources/{resource_id}/canvas-refs")
async def resource_canvas_refs(resource_id: str, auth: AuthDep):
    # Resource ownership/membership gate (same helper the asset-AI batch uses).
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=404, detail="resource not found")
    repo = CanvasRefsRepository()
    items = await repo.list_canvases_for_resource(resource_id)
    return {"success": True, "data": items, "count": len(items)}
