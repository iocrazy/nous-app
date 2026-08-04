"""Script Projects Router — CRUD endpoints for script projects."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, require_team_id
from app.core.scope_guards import verify_script_access
from app.repositories.episode_repository import get_episode_repository
from app.repositories.script_repository import get_script_project_repository
from app.repositories.script_scene_repository import get_script_scene_repository
from app.schemas.script import ScriptProjectCreate, ScriptProjectUpdate, ViewportUpdate
from app.services.storyboard.script.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


async def _assert_same_project_episode(script_id: str, episode_id: str) -> None:
    """Reassign guard: the target episode must belong to the SAME project as the
    script. Snowflake project ids are native ints on the row but str on the
    body, so both sides are ``str()``-coerced (#1006). Any mismatch — or a
    missing script / episode — is a 404 (do not leak cross-project existence)."""
    project = await get_script_project_repository().get_by_id(script_id)
    episode = await get_episode_repository().get_by_id(episode_id)
    if (
        not project
        or not episode
        or str(project.get("project_id")) != str(episode.get("project_id"))
    ):
        raise HTTPException(
            status_code=404, detail="Episode not in this script's project"
        )


async def _assert_episode_unowned(script_id: str, episode_id: str) -> None:
    """Reassign guard (agent-layer spec §4.3's episode-scoped scene-number
    uniqueness): reject reassigning to an episode ANOTHER live script
    already owns. The auto-provision path (``get_or_create_for_episode``)
    enforces "at most one script per episode" via an advisory lock; this
    manual reassign path (a plain ``UPDATE``) had NO such check at all —
    two scripts could end up on one episode, each numbering its own scenes
    1, 2, 3..., producing two "scene 1" in the same episode (the exact
    laper.ai collision A3 exists to prevent, reached through a different
    door). 409, not 404 — the episode's existence isn't in question, only
    whether it's free to attach to."""
    owner = await get_script_project_repository().get_by_episode(episode_id)
    if owner is not None and str(owner.get("id")) != str(script_id):
        raise HTTPException(
            status_code=409, detail="Episode already has another script attached"
        )


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
            episode_id=body.episode_id,
        )
        return {"success": True, "data": project}
    except Exception as exc:
        logger.error(f"[Scripts] create_project failed: {exc}")
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
        logger.error(f"[Scripts] list_projects failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list scripts")


@router.get("/{script_id}")
async def get_script_project(
    auth: AuthDep,
    script_id: str,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        project = await svc.get_project_full(script_id)
        if not project:
            raise HTTPException(status_code=404, detail="Script project not found")
        return {"success": True, "data": project}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Scripts] get_project {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get script")


@router.put("/{script_id}")
async def update_script_project(
    auth: AuthDep,
    script_id: str,
    body: ScriptProjectUpdate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        data = body.model_dump(exclude_none=True)
        # Reassigning to another episode must stay within the script's
        # project AND that episode must not already be owned by a DIFFERENT
        # live script (episode-scoped scene numbering's uniqueness promise).
        if data.get("episode_id") is not None:
            await _assert_same_project_episode(script_id, data["episode_id"])
            await _assert_episode_unowned(script_id, data["episode_id"])
        updated = await svc.update_project(script_id, data)
        return {"success": True, "data": updated}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Scripts] update_project {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update script")


@router.delete("/{script_id}")
async def delete_script_project(
    auth: AuthDep,
    script_id: str,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.soft_delete_project(script_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Scripts] delete_project {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete script")


@router.patch("/{script_id}/viewport")
async def update_viewport(
    auth: AuthDep,
    script_id: str,
    body: ViewportUpdate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.update_viewport(script_id, body.model_dump())
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Scripts] update_viewport {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update viewport")


@router.post("/{script_id}/lock-numbering")
async def lock_numbering(
    auth: AuthDep,
    script_id: str,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Freeze scene numbering (agent-layer spec §4.2 "锁定拍摄稿"). Derives
    every scene's number from its current order and writes it permanently;
    idempotent — locking an already-locked script is a no-op."""
    try:
        result = await get_script_scene_repository().lock_numbering(script_id)
        return {"success": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"[Scripts] lock_numbering {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to lock numbering")
