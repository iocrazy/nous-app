"""Script Shots Router — shot CRUD + reorder (Phase B P3).

Endpoints:
  GET  /scenes/{scene_id}/shots   — verify_scene_read_access
  POST /scenes/{scene_id}/shots   — verify_scene_access
  GET    /shots/{shot_id}         — verify_shot_read_access
  PATCH  /shots/{shot_id}         — verify_shot_access
  DELETE /shots/{shot_id}         — verify_shot_access
  POST /shots/{shot_id}/move      — verify_shot_access

The GET routes use the *_read_access variants (team membership OR an
explicit project_members row on the parent project — 2026-08-12 fix); the
write routes (including PATCH — a viewer-role project member still 403s)
keep the team-only gate unchanged.

Shots are the storyboard tier that hangs off a scene. This router owns the
manual CRUD + sparse reorder; Auto Storyboard (AI breakdown) and single-shot
Generate land in Task 3 as their own dispatch endpoints. ``status`` and the
produced media URLs are NOT client-settable here — ``PATCH`` writes the
parameter-tag / description whitelist only (the repository's ``update`` lane),
and the status machine flows through the generate workflow.
"""

import uuid as _uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.config import settings
from app.core.deps import AuthDep
from app.core.scope_guards import (
    verify_scene_access,
    verify_scene_read_access,
    verify_shot_access,
    verify_shot_read_access,
)
from app.repositories.script_shot_repository import get_script_shot_repository
from app.schemas.script import ShotCreate, ShotMoveRequest, ShotUpdate
from app.services.infra.unified_task_manager import get_task_manager

router = APIRouter()

# Snowflake BIGINT id/FK fields on a script_shots row (mirrors the
# repository's `_SHOT_BIGINT_FIELDS` write-side coercion; `id` itself is the
# PK and always bigint). The repository deliberately keeps these NATIVE INT
# on read (strategy-C parity, see script_shot_repository.py) for internal
# comparisons — this stringifies ONLY at the JSON response boundary.
#
# 2026-08 P0: a Snowflake id > 2^53 serialized as a JSON *number* loses
# precision in JS, and even below that threshold the frontend's node-id
# reconciliation matches shot ids as strings — a bare int response silently
# mismatched and caused the storyboard canvas to re-mount (duplicate,
# hidden) shot nodes on every reconcile. The frontend's `readId` (canvas
# shotSync) already tolerates BOTH number and string, so this is a
# backward-compatible stringify, not a breaking contract change.
_SHOT_ID_FIELDS = ("id", "scene_id", "created_by_agent_run_id")


def _to_response(shot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Stringify a shot row's bigint id/FK fields for the JSON response.

    Returns a NEW dict (never mutates the repository's row) so any caller
    still holding the original dict for internal use is unaffected. ``None``
    passes through (a 404-shaped "not found" caller checks before this)."""
    if shot is None:
        return None
    out = dict(shot)
    for field in _SHOT_ID_FIELDS:
        if field in out and out[field] is not None:
            out[field] = str(out[field])
    return out


def _to_response_list(shots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``_to_response`` applied to every row in a list."""
    return [_to_response(s) for s in shots]


@router.get("/scenes/{scene_id}/shots")
async def list_shots(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_read_access),
) -> Dict[str, Any]:
    """List all shots for a scene, ordered by sort_order."""
    try:
        shots = await get_script_shot_repository().list_by_scene(scene_id)
        return {"success": True, "data": _to_response_list(shots)}
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
        return {"success": True, "data": _to_response(shot)}
    except Exception as exc:
        logger.error(f"[Shots] create for scene {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create shot")


@router.get("/shots/{shot_id}")
async def get_shot(
    shot_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_shot_read_access),
) -> Dict[str, Any]:
    """Get a single shot by id."""
    try:
        shot = await get_script_shot_repository().get_by_id(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="Shot not found")
        return {"success": True, "data": _to_response(shot)}
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
        return {"success": True, "data": _to_response(shot)}
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
        return {"success": True, "data": _to_response(shot)}
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
                # 3a: no agent run behind a human click. Explicit None so
                # "this lane has no run" is written down, not forgotten.
                "run_id": None,
                "turn": None,
                "step": None,
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


@router.post("/shots/{shot_id}/generate-video")
async def generate_shot_video(
    shot_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_shot_access),
) -> Dict[str, Any]:
    """Dispatch async single-shot video generation (flag-gated).

    - flag ``FEATURE_SHOT_VIDEO`` off → 404 (endpoint existence hidden).
    - Unlike ``/generate`` this endpoint does NOT flip ``shot.status``: the
      ``status`` column is the IMAGE lane's state machine and a video run must
      not clobber it. The video lifecycle lives in ``task_tracking``
      (task_type='shot_video') and the workflow writes only ``shot.video_url`` on
      success (see script_shot_video docstring). With no status flip there is
      nothing to roll back on dispatch failure — the 500 + the task row are the
      surface.

    LOW (known, accepted): ``verify_shot_access`` runs BEFORE this body, so a
    caller without access gets 403/404 regardless of the flag — that leaks
    nothing about the flag (access-scoped, not existence-scoped)."""
    if not settings.FEATURE_SHOT_VIDEO:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="shot_video",  # ≤20 chars: task_tracking.task_type is VARCHAR(20)
            title="Generate shot video",
            dbos_workflow_id=wf_id,
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_shot_video import script_shot_video_workflow

        await start_workflow_routed(
            "script_shot_video",
            dbos_workflow_callable=script_shot_video_workflow,
            dbos_workflow_kwargs={
                "shot_id": shot_id,
                "user_id": auth.user_id,
                # 3a: no agent run behind a human click. Explicit None so
                # "this lane has no run" is written down, not forgotten.
                "run_id": None,
                "turn": None,
                "step": None,
            },
            workflow_id=wf_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error(f"[Shots] generate-video {shot_id} dispatch failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to dispatch shot video")
