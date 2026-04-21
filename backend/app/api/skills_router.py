"""Skills Router — CRUD endpoints for AI skills."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user, require_team_id
from app.repositories.skill_repository import SkillRepository
from app.schemas.skill import SkillCreate, SkillUpdate

router = APIRouter(prefix="/skills")

VALID_CATEGORIES = ["script", "storyboard", "copywriting", "general"]


@router.post("")
async def create_skill(auth: AuthDep, body: SkillCreate) -> Dict[str, Any]:
    """Create a new skill (requires auth + team membership)."""
    try:
        repo = SkillRepository()
        team_id = await require_team_id(auth.user_id)
        data = {
            **body.model_dump(exclude_none=True),
            "team_id": team_id,
            "created_by": auth.user_id,
            "status": "active",
        }
        skill = await repo.create(data)
        return {"success": True, "data": skill}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create skill")


@router.get("")
async def list_skills(
    auth: AuthDep,
    project_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """List skills: own team's + system presets + public. Returns summary (no content_md)."""
    try:
        repo = SkillRepository()
        team_id = await get_team_id_for_user(auth.user_id)
        skills = await repo.list_skills(
            team_id=team_id,
            project_id=project_id,
            category=category,
        )
        return {"success": True, "data": skills}
    except Exception as exc:
        logger.error("[Skills] list failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list skills")


@router.get("/categories")
async def list_categories() -> Dict[str, Any]:
    """List available skill categories."""
    return {"success": True, "data": VALID_CATEGORIES}


@router.get("/{skill_id}")
async def get_skill(auth: AuthDep, skill_id: str) -> Dict[str, Any]:
    """Get full skill detail (includes content_md)."""
    try:
        repo = SkillRepository()
        skill = await repo.get_by_id(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {"success": True, "data": skill}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] get %s failed: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="Failed to get skill")


@router.patch("/{skill_id}")
async def update_skill(
    auth: AuthDep, skill_id: str, body: SkillUpdate
) -> Dict[str, Any]:
    """Update a skill (owner only)."""
    try:
        repo = SkillRepository()
        existing = await repo.get_by_id(skill_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        if existing.get("team_id") is None:
            raise HTTPException(status_code=403, detail="Cannot modify system presets")
        if existing.get("created_by") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Only the skill owner can update"
            )
        skill = await repo.update(skill_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": skill}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] update %s failed: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update skill")


@router.delete("/{skill_id}")
async def delete_skill(auth: AuthDep, skill_id: str) -> Dict[str, Any]:
    """Archive a skill (owner only, system presets protected)."""
    try:
        repo = SkillRepository()
        existing = await repo.get_by_id(skill_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        if existing.get("team_id") is None:
            raise HTTPException(status_code=403, detail="Cannot delete system presets")
        if existing.get("created_by") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Only the skill owner can delete"
            )
        await repo.archive(skill_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] delete %s failed: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete skill")
