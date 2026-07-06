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

import uuid as _uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.config import settings
from app.core.deps import AuthDep
from app.core.scope_guards import verify_scene_access, verify_shot_access
from app.repositories.script_shot_repository import get_script_shot_repository
from app.schemas.script import ShotCreate, ShotMoveRequest, ShotUpdate
from app.services.infra.unified_task_manager import get_task_manager

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


@router.post("/scenes/{scene_id}/auto-storyboard")
async def auto_storyboard(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Dispatch async AI breakdown of a scene into a storyboard shot list.

    Returns the flat ``{"success", "task_id"}`` envelope immediately; the
    breakdown workflow reads the scene elements, asks the model for 3-8 shots,
    and persists them (status='empty') in one transaction."""
    try:
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="shot_breakdown",  # ≤20 chars: task_tracking.task_type is VARCHAR(20)
            title="Auto storyboard scene",
            dbos_workflow_id=wf_id,
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_shot_breakdown import (
            script_shot_breakdown_workflow,
        )

        await start_workflow_routed(
            "script_shot_breakdown",
            dbos_workflow_callable=script_shot_breakdown_workflow,
            dbos_workflow_kwargs={
                "scene_id": scene_id,
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error(f"[Shots] auto-storyboard for scene {scene_id} failed: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to dispatch auto-storyboard"
        )


@router.post("/shots/{shot_id}/generate")
async def generate_shot(
    shot_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_shot_access),
) -> Dict[str, Any]:
    """Dispatch async single-shot image generation (flag-gated).

    - flag ``FEATURE_SHOT_GENERATE`` off → 404 (endpoint existence hidden).
    - sets ``status='generating'`` before dispatch; the workflow flips it to
      'done' + image_url on success, or 'failed' on error.

    LOW (known, accepted): ``verify_shot_access`` is a Depends and runs BEFORE
    this body, so a caller WITHOUT shot access gets the guard's 403/404 whether
    or not the flag is on — that leaks nothing about the flag (403/404 is
    access-scoped, not existence-scoped) and the 404-hides-existence guarantee
    still holds for callers WITH access."""
    if not settings.FEATURE_SHOT_GENERATE:
        raise HTTPException(status_code=404, detail="Not Found")
    repo = get_script_shot_repository()
    await repo.update_status(shot_id, "generating")
    try:
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="shot_generate",  # ≤20 chars: task_tracking.task_type is VARCHAR(20)
            title="Generate shot image",
            dbos_workflow_id=wf_id,
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_shot_generate import script_shot_generate_workflow

        await start_workflow_routed(
            "script_shot_generate",
            dbos_workflow_callable=script_shot_generate_workflow,
            dbos_workflow_kwargs={
                "shot_id": shot_id,
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        # Dispatch failed AFTER we flipped status to 'generating' but BEFORE the
        # workflow ever ran — roll back to 'empty' (the honest initial state; the
        # generate workflow, not this endpoint, owns the 'failed' state). Without
        # this the shot would be stuck 'generating' forever with no live task.
        logger.error(f"[Shots] generate {shot_id} dispatch failed: {exc}")
        try:
            await repo.update_status(shot_id, "empty")
        except Exception as rollback_exc:  # noqa: BLE001
            logger.error(
                f"[Shots] generate {shot_id} status rollback failed: {rollback_exc}"
            )
        raise HTTPException(status_code=500, detail="Failed to dispatch shot generate")
