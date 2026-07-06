"""Script Shots Router — shot CRUD + reorder (Phase B P3).

Endpoints:
  GET  /scenes/{scene_id}/shots   — verify_scene_access
  POST /scenes/{scene_id}/shots   — verify_scene_access
  GET    /shots/{shot_id}         — verify_shot_access
  PATCH  /shots/{shot_id}         — verify_shot_access
  DELETE /shots/{shot_id}         — verify_shot_access
  POST /shots/{shot_id}/move      — verify_shot_access

Shots are the storyboard tier that hangs off a scene. This router owns the
manual CRUD + sparse reorder; Auto Storyboard (AI breakdown) and single-shot
Generate land in Task 3 as their own dispatch endpoints. ``status`` and the
produced media URLs are NOT client-settable here — ``PATCH`` writes the
parameter-tag / description whitelist only (the repository's ``update`` lane),
and the status machine flows through the generate workflow.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_scene_access, verify_shot_access
from app.repositories.script_shot_repository import get_script_shot_repository
from app.schemas.script import ShotCreate, ShotMoveRequest, ShotUpdate

router = APIRouter()


@router.get("/scenes/{scene_id}/shots")
async def list_shots(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """List all shots for a scene, ordered by sort_order."""
    try:
        shots = await get_script_shot_repository().list_by_scene(scene_id)
        return {"success": True, "data": shots}
    except Exception as exc:
        logger.error(f"[Shots] list for scene {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list shots")


@router.post("/scenes/{scene_id}/shots")
async def create_shot(
    scene_id: str,
    auth: AuthDep,
    body: ShotCreate,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Create a shot under a scene. sort_order auto-assigns to MAX+STEP within
    the scene when omitted; a fresh shot lands status='empty'."""
    try:
        data = body.model_dump(exclude_none=True)
        data["scene_id"] = scene_id
        shot = await get_script_shot_repository().create(data)
        return {"success": True, "data": shot}
    except Exception as exc:
        logger.error(f"[Shots] create for scene {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create shot")


@router.get("/shots/{shot_id}")
async def get_shot(
    shot_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_shot_access),
) -> Dict[str, Any]:
    """Get a single shot by id."""
    try:
        shot = await get_script_shot_repository().get_by_id(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="Shot not found")
        return {"success": True, "data": shot}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Shots] get {shot_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get shot")


@router.patch("/shots/{shot_id}")
async def update_shot(
    shot_id: str,
    auth: AuthDep,
    body: ShotUpdate,
    _guard: None = Depends(verify_shot_access),
) -> Dict[str, Any]:
    """Update shot parameter tags / description. NEVER touches status or URLs."""
    try:
        shot = await get_script_shot_repository().update(
            shot_id, body.model_dump(exclude_none=True)
        )
        if shot is None:
            raise HTTPException(status_code=404, detail="Shot not found")
        return {"success": True, "data": shot}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Shots] update {shot_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update shot")


@router.delete("/shots/{shot_id}")
async def delete_shot(
    shot_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_shot_access),
) -> Dict[str, Any]:
    """Delete a shot."""
    try:
        await get_script_shot_repository().delete(shot_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Shots] delete {shot_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete shot")


@router.post("/shots/{shot_id}/move")
async def move_shot(
    shot_id: str,
    auth: AuthDep,
    body: ShotMoveRequest,
    _guard: None = Depends(verify_shot_access),
) -> Dict[str, Any]:
    """Reorder a shot within its scene (sparse insertion between anchors)."""
    try:
        shot = await get_script_shot_repository().move_shot(
            shot_id,
            before_shot_id=body.before_shot_id,
            after_shot_id=body.after_shot_id,
        )
        return {"success": True, "data": shot}
    except Exception as exc:
        logger.error(f"[Shots] move {shot_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to move shot")
