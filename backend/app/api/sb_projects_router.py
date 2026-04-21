# backend/app/api/sb_projects_router.py

"""
Storyboard Projects Router

CRUD endpoints for storyboard projects: create, list, get, update, soft-delete,
and viewport persistence.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, require_team_id
from app.schemas.storyboard import StoryboardProjectCreate, StoryboardProjectUpdate
from app.services.storyboard_service import StoryboardService

router = APIRouter(prefix="/storyboard/projects")


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------


@router.post("")
async def create_project(
    auth: AuthDep, body: StoryboardProjectCreate
) -> Dict[str, Any]:
    """Create a new storyboard project for the authenticated user's team."""
    team_id = await require_team_id(auth.user_id)
    try:
        svc = StoryboardService()
        project = await svc.create_project(
            team_id=team_id,
            user_id=auth.user_id,
            name=body.name,
            description=body.description,
            project_id=body.project_id,
        )
        return {"success": True, "data": project}
    except Exception as exc:
        logger.error("[SBProjects] create_project failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create project: {exc}")


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get("")
async def list_projects(
    auth: AuthDep,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=200),
    sort_by: str = Query("updated_at", pattern="^(updated_at|created_at|name)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    project_id: Optional[int] = Query(None),
) -> Dict[str, Any]:
    """List storyboard projects for the authenticated user's team."""
    team_id = await require_team_id(auth.user_id)
    try:
        svc = StoryboardService()
        result = await svc.list_projects(
            team_id=team_id,
            page=page,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            project_id=project_id,
        )
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("[SBProjects] list_projects failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {exc}")


# ---------------------------------------------------------------------------
# GET /{project_id}
# ---------------------------------------------------------------------------


@router.get("/{project_id}")
async def get_project(auth: AuthDep, project_id: str) -> Dict[str, Any]:
    """Retrieve a single storyboard project with its full canvas data."""
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        project = await svc.get_project_full(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"success": True, "data": project}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[SBProjects] get_project %s failed: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to get project: {exc}")


# ---------------------------------------------------------------------------
# PUT /{project_id}
# ---------------------------------------------------------------------------


@router.put("/{project_id}")
async def update_project(
    auth: AuthDep, project_id: str, body: StoryboardProjectUpdate
) -> Dict[str, Any]:
    """Update metadata for an existing storyboard project."""
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        updated = await svc.update_project(
            project_id,
            body.model_dump(exclude_none=True),
        )
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error("[SBProjects] update_project %s failed: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to update project: {exc}")


# ---------------------------------------------------------------------------
# DELETE /{project_id}
# ---------------------------------------------------------------------------


@router.delete("/{project_id}")
async def delete_project(auth: AuthDep, project_id: str) -> Dict[str, Any]:
    """Soft-delete a storyboard project (sets status to 'deleted')."""
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        await svc.soft_delete_project(project_id)
        return {"success": True}
    except Exception as exc:
        logger.error("[SBProjects] delete_project %s failed: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}")


# ---------------------------------------------------------------------------
# PUT /{project_id}/viewport
# ---------------------------------------------------------------------------


@router.patch("/{project_id}/viewport")
@router.put("/{project_id}/viewport")
async def update_viewport(
    auth: AuthDep, project_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Persist the canvas viewport state (pan / zoom) for a project."""
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        updated = await svc.update_viewport(project_id, body)
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error("[SBProjects] update_viewport %s failed: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to update viewport: {exc}")
