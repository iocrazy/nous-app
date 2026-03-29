"""Style Templates Router — CRUD endpoints for prompt style templates."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user, require_team_id
from app.repositories.style_template_repository import StyleTemplateRepository
from app.schemas.style_template import StyleTemplateCreate, StyleTemplateUpdate

router = APIRouter(prefix="/style-templates")

_repo = StyleTemplateRepository()


@router.post("/")
async def create_style_template(
    auth: AuthDep, body: StyleTemplateCreate
) -> Dict[str, Any]:
    """Create a new style template (requires auth + team membership)."""
    try:
        team_id = await require_team_id(auth.user_id)
        data = {
            **body.model_dump(exclude_none=True),
            "team_id": team_id,
            "created_by": auth.user_id,
        }
        template = await _repo.create(data)
        return {"success": True, "data": template}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[StyleTemplates] create failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to create style template: {exc}"
        )


@router.get("/")
async def list_style_templates(
    auth: AuthDep,
    category: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """List style templates: own team's templates + public templates."""
    try:
        team_id = await get_team_id_for_user(auth.user_id)
        templates = await _repo.list_templates(
            team_id=team_id,
            category=category,
            include_public=True,
        )
        return {"success": True, "data": templates}
    except Exception as exc:
        logger.error("[StyleTemplates] list failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to list style templates: {exc}"
        )


@router.put("/{template_id}")
async def update_style_template(
    auth: AuthDep, template_id: str, body: StyleTemplateUpdate
) -> Dict[str, Any]:
    """Update a style template (owner only)."""
    try:
        existing = await _repo.get_by_id(template_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Style template not found")
        if existing.get("created_by") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Only the template owner can update"
            )

        template = await _repo.update(
            template_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": template}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "[StyleTemplates] update %s failed: %s", template_id, exc
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to update style template: {exc}"
        )


@router.delete("/{template_id}")
async def delete_style_template(
    auth: AuthDep, template_id: str
) -> Dict[str, Any]:
    """Delete a style template (owner only)."""
    try:
        existing = await _repo.get_by_id(template_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Style template not found")
        if existing.get("created_by") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Only the template owner can delete"
            )

        await _repo.delete(template_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "[StyleTemplates] delete %s failed: %s", template_id, exc
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to delete style template: {exc}"
        )
