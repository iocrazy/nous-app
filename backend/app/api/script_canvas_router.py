"""Script Canvas Router — chapter node CRUD and canvas sync endpoints."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_script_access
from app.schemas.script import (
    ScriptCanvasSyncRequest,
    ScriptChapterCreate,
    ScriptChapterUpdate,
)
from app.services.storyboard.script.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


async def _script_id_for_chapter(chapter_id: str) -> str:
    """Resolve a chapter to its owning script_id (404 if the chapter is gone).

    Used by the chapter update/delete IDOR fix: those endpoints have no
    script_id in their path, so we look it up before the team check."""
    chapter = await ScriptService().chapter_repo.get_by_id(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return str(chapter.get("script_id"))


@router.post("/{script_id}/chapters")
async def create_chapter(
    auth: AuthDep, script_id: str, body: ScriptChapterCreate
) -> Dict[str, Any]:
    try:
        await verify_script_access(script_id, auth)
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
    # IDOR fix: resolve chapter → script and enforce team access BEFORE the
    # try/except (which maps everything to 500) so 404/403 surface intact.
    script_id = await _script_id_for_chapter(chapter_id)
    await verify_script_access(script_id, auth)
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
    # IDOR fix: same resolve-then-verify guard as update_chapter.
    script_id = await _script_id_for_chapter(chapter_id)
    await verify_script_access(script_id, auth)
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
        await verify_script_access(script_id, auth)
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
