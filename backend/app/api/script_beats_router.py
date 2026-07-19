"""Script Beats Router — beat CRUD + reorder (Beats view, PR-BT1).

Endpoints:
  GET  /scripts/{script_id}/beats  — verify_script_access
  POST /scripts/{script_id}/beats  — verify_script_access
  PATCH  /beats/{beat_id}          — verify_beat_access
  DELETE /beats/{beat_id}          — verify_beat_access
  POST /beats/{beat_id}/move       — verify_beat_access

A classic beat sheet: ordered beat cards (title + summary + optional linked
scene ids) hanging off a script. Pure synchronous CRUD — no AI, no workflow, no
task_type. Scene links are stored as an ordered JSONB string array on the beat.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_beat_access, verify_script_access
from app.repositories.script_beat_repository import get_script_beat_repository
from app.schemas.script import BeatCreate, BeatMoveRequest, BeatUpdate

router = APIRouter()


@router.get("/scripts/{script_id}/beats")
async def list_beats(
    script_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """List all beats for a script, ordered by sort_order."""
    try:
        beats = await get_script_beat_repository().list_by_script(script_id)
        return {"success": True, "data": beats}
    except Exception as exc:
        logger.error(f"[Beats] list for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list beats")


@router.post("/scripts/{script_id}/beats")
async def create_beat(
    script_id: str,
    auth: AuthDep,
    body: BeatCreate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Create a beat under a script. sort_order auto-assigns to MAX+STEP within
    the script when omitted."""
    try:
        data = body.model_dump(exclude_none=True)
        data["script_id"] = script_id
        beat = await get_script_beat_repository().create(data)
        return {"success": True, "data": beat}
    except Exception as exc:
        logger.error(f"[Beats] create for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create beat")


@router.patch("/beats/{beat_id}")
async def update_beat(
    beat_id: str,
    auth: AuthDep,
    body: BeatUpdate,
    _guard: None = Depends(verify_beat_access),
) -> Dict[str, Any]:
    """Update a beat's title / summary / linked scenes / arrangement fields."""
    try:
        # exclude_unset (NOT exclude_none): true PATCH semantics — an absent
        # field stays untouched while an explicit null clears the column
        # (summary / duration_sec / color). title is NOT NULL, so a null there
        # is dropped rather than forwarded as a NULL write.
        data = body.model_dump(exclude_unset=True)
        # title is NOT NULL; scene_ids-null would coerce to [] and silently wipe
        # all links. Both are "untouched", never destructive, when sent as null.
        for immutable_via_null in ("title", "scene_ids"):
            if data.get(immutable_via_null) is None:
                data.pop(immutable_via_null, None)
        beat = await get_script_beat_repository().update(beat_id, data)
        if beat is None:
            raise HTTPException(status_code=404, detail="Beat not found")
        return {"success": True, "data": beat}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Beats] update {beat_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update beat")


@router.delete("/beats/{beat_id}")
async def delete_beat(
    beat_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_beat_access),
) -> Dict[str, Any]:
    """Delete a beat."""
    try:
        await get_script_beat_repository().delete(beat_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Beats] delete {beat_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete beat")


@router.post("/beats/{beat_id}/move")
async def move_beat(
    beat_id: str,
    auth: AuthDep,
    body: BeatMoveRequest,
    _guard: None = Depends(verify_beat_access),
) -> Dict[str, Any]:
    """Reorder a beat within its script (sparse insertion after an anchor)."""
    try:
        beat = await get_script_beat_repository().move(
            beat_id, after_beat_id=body.after_beat_id
        )
        return {"success": True, "data": beat}
    except Exception as exc:
        logger.error(f"[Beats] move {beat_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to move beat")
