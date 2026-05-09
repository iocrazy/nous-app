# backend/app/api/sb_characters_router.py

"""
Storyboard Characters Router

CRUD endpoints for character management within storyboard projects.
Characters store visual-trait data used as prompt fragments for AI generation.
"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.storyboard_repository import StoryboardCharacterRepository
from app.schemas.storyboard import CharacterCreate, CharacterUpdate
from app.services.storyboard.storyboard_service import StoryboardService

router = APIRouter(prefix="/storyboard")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/characters
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/characters")
async def create_character(
    auth: AuthDep,
    project_id: str,
    body: CharacterCreate,
) -> Dict[str, Any]:
    """Create a character entry for a storyboard project."""
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        character = await svc.create_character(
            project_id=project_id,
            name=body.name,
            description=body.description,
            visual_traits=body.visual_traits,
        )
        return {"success": True, "data": character}
    except Exception as exc:
        logger.error(
            "[SBCharacters] create_character project=%s failed: %s", project_id, exc
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to create character: {exc}"
        )


# ---------------------------------------------------------------------------
# PUT /characters/{char_id}
# ---------------------------------------------------------------------------


@router.put("/characters/{char_id}")
async def update_character(
    auth: AuthDep,
    char_id: str,
    body: CharacterUpdate,
) -> Dict[str, Any]:
    """Update a character's name, description, or visual traits."""
    try:
        svc = StoryboardService()
        updated = await svc.update_character(
            char_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error(f"[SBCharacters] update_character {char_id} failed: {exc}")
        raise HTTPException(
            status_code=500, detail=f"Failed to update character: {exc}"
        )


# ---------------------------------------------------------------------------
# DELETE /characters/{char_id}
# ---------------------------------------------------------------------------


@router.delete("/characters/{char_id}")
async def delete_character(auth: AuthDep, char_id: str) -> Dict[str, Any]:
    """Hard-delete a character."""
    try:
        svc = StoryboardService()
        await svc.delete_character(char_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[SBCharacters] delete_character {char_id} failed: {exc}")
        raise HTTPException(
            status_code=500, detail=f"Failed to delete character: {exc}"
        )


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/characters
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/characters")
async def list_characters(
    auth: AuthDep,
    project_id: str,
) -> Dict[str, Any]:
    """List all characters for a storyboard project."""
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        character_repo = StoryboardCharacterRepository()
        characters = await character_repo.list_by_project(project_id)
        return {"success": True, "data": characters}
    except Exception as exc:
        logger.error(
            "[SBCharacters] list_characters project=%s failed: %s", project_id, exc
        )
        raise HTTPException(status_code=500, detail=f"Failed to list characters: {exc}")
