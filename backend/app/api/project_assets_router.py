# backend/app/api/project_assets_router.py
"""Project Assets read endpoint (IC-port P4).

  GET /resources/{id}/canvas-refs    — canvases that reference a resource

The read JOINs with the caller's membership/ownership filter so the
RLS-locked refs table never leaks cross-tenant rows.

``GET /canvases/{id}/assets`` and ``GET /resources/project-assets/tree`` were
removed in the P4 OpenAPI typing pass (2026-09-24): no client called either
since the Project Assets view retired (P1 generated inbox).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.media_permissions import check_media_access
from app.core.deps import AuthDep
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.schemas.canvas_responses import ResourceCanvasRefsEnvelope
from app.services.modules.gate import require_module

router = APIRouter(dependencies=[Depends(require_module("projects"))])


@router.get(
    "/resources/{resource_id}/canvas-refs", response_model=ResourceCanvasRefsEnvelope
)
async def resource_canvas_refs(resource_id: str, auth: AuthDep):
    # Resource ownership/membership gate (same helper the asset-AI batch uses).
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=404, detail="resource not found")
    repo = CanvasRefsRepository()
    items = await repo.list_canvases_for_resource(resource_id)
    return {"success": True, "data": items, "count": len(items)}
