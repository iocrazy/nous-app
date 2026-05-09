"""Script Canvas Router — chapter node CRUD and canvas sync endpoints."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user
from app.schemas.script import (
    ScriptCanvasSyncRequest,
    ScriptChapterCreate,
    ScriptChapterUpdate,
)
from app.services.storyboard.script.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


async def _verify_script_access(script_id: str, user_id: str) -> None:
    """Verify the authenticated user has access to this script (via team membership)."""
    svc = ScriptService()
    project = await svc.project_repo.get_by_id(script_id)
    if not project:
        raise HTTPException(status_code=404, detail="Script project not found")
    user_team = await get_team_id_for_user(user_id)
    if str(user_team) != str(project.get("team_id")):
        raise HTTPException(status_code=403, detail="Access denied")


@router.post("/{script_id}/chapters")
async def create_chapter(
    auth: AuthDep, script_id: str, body: ScriptChapterCreate
) -> Dict[str, Any]:
    try:
        await _verify_script_access(script_id, auth.user_id)
        svc = ScriptService()
        chapter = await svc.create_chapter(
            script_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": chapter}
    except Exception as exc:
        logger.error(f"[Scripts] create_chapter failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create chapter")


@router.put("/chapters/{chapter_id}")
async def update_chapter(
    auth: AuthDep, chapter_id: str, body: ScriptChapterUpdate
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        chapter = await svc.update_chapter(
            chapter_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": chapter}
    except Exception as exc:
        logger.error(f"[Scripts] update_chapter {chapter_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update chapter")


@router.delete("/chapters/{chapter_id}")
async def delete_chapter(auth: AuthDep, chapter_id: str) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.delete_chapter(chapter_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Scripts] delete_chapter {chapter_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete chapter")


@router.post("/{script_id}/canvas/sync")
async def sync_canvas(
    auth: AuthDep, script_id: str, body: ScriptCanvasSyncRequest
) -> Dict[str, Any]:
    try:
        await _verify_script_access(script_id, auth.user_id)
        svc = ScriptService()
        added = [ch.model_dump(exclude_none=True) for ch in body.added_chapters]
        result = await svc.sync_canvas(
            script_id=script_id,
            added=added,
            updated=body.updated_chapters,
            deleted_ids=body.deleted_chapter_ids,
        )
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error(f"[Scripts] sync_canvas {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to sync canvas")
