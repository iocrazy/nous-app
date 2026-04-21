"""Script Projects Router — CRUD endpoints for script projects."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, require_team_id
from app.schemas.script import ScriptProjectCreate, ScriptProjectUpdate, ViewportUpdate
from app.services.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


@router.post("")
async def create_script_project(
    auth: AuthDep, body: ScriptProjectCreate
) -> Dict[str, Any]:
    team_id = await require_team_id(auth.user_id)
    try:
        svc = ScriptService()
        project = await svc.create_project(
            team_id=team_id,
            user_id=auth.user_id,
            project_id=body.project_id,
            name=body.name,
            description=body.description,
        )
        return {"success": True, "data": project}
    except Exception as exc:
        logger.error("[Scripts] create_project failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create script")


@router.get("")
async def list_script_projects(
    auth: AuthDep,
    project_id: int = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=200),
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        result = await svc.list_projects(
            project_id=project_id,
            page=page,
            limit=limit,
            search=search,
        )
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("[Scripts] list_projects failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list scripts")


@router.get("/{script_id}")
async def get_script_project(auth: AuthDep, script_id: str) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        project = await svc.get_project_full(script_id)
        if not project:
            raise HTTPException(status_code=404, detail="Script project not found")
        return {"success": True, "data": project}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Scripts] get_project %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail="Failed to get script")


@router.put("/{script_id}")
async def update_script_project(
    auth: AuthDep, script_id: str, body: ScriptProjectUpdate
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        updated = await svc.update_project(
            script_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error("[Scripts] update_project %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update script")


@router.delete("/{script_id}")
async def delete_script_project(auth: AuthDep, script_id: str) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.soft_delete_project(script_id)
        return {"success": True}
    except Exception as exc:
        logger.error("[Scripts] delete_project %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete script")


@router.patch("/{script_id}/viewport")
async def update_viewport(
    auth: AuthDep, script_id: str, body: ViewportUpdate
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.update_viewport(script_id, body.model_dump())
        return {"success": True}
    except Exception as exc:
        logger.error("[Scripts] update_viewport %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update viewport")
