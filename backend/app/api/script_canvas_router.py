"""Script Canvas Router — chapter node CRUD and canvas sync endpoints."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.api.row_guard import require_row
from app.core.deps import AuthDep
from app.core.scope_guards import verify_script_access
from app.schemas.envelope import Envelope
from app.schemas.script import (
    ScriptCanvasSyncRequest,
    ScriptChapterCreate,
    ScriptChapterUpdate,
)
from app.schemas.script_project_responses import (
    ScriptAck,
    ScriptCanvasSyncResult,
    ScriptChapterRow,
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


async def _assert_chapters_in_script(
    script_id: str, body: ScriptCanvasSyncRequest
) -> None:
    """Sync guard: every chapter id the body updates or deletes must belong to
    ``script_id``. The access check covers the path's script only, and the
    service writes by chapter id alone — without this a caller with access to
    one script could rewrite or delete any other script's chapters. 404 (do
    not leak which ids exist elsewhere)."""
    targets = {str(cid) for cid in body.deleted_chapter_ids}
    targets |= {str(ch["id"]) for ch in body.updated_chapters if ch.get("id")}
    if not targets:
        return
    owned = await ScriptService().chapter_repo.get_by_script(script_id)
    if targets - {str(ch.get("id")) for ch in owned}:
        raise HTTPException(status_code=404, detail="Chapter not in this script")


@router.post("/{script_id}/chapters", response_model=Envelope[ScriptChapterRow])
async def create_chapter(
    auth: AuthDep, script_id: str, body: ScriptChapterCreate
) -> Dict[str, Any]:
    # Outside the try below (which maps everything to 500) so 404/403 surface;
    # inside it, a denied caller used to get a 500.
    await verify_script_access(script_id, auth)
    try:
        svc = ScriptService()
        chapter = await svc.create_chapter(
            script_id, body.model_dump(exclude_none=True)
        )
        return {"success": True, "data": chapter}
    except Exception as exc:
        logger.error(f"[Scripts] create_chapter failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create chapter")


@router.put("/chapters/{chapter_id}", response_model=Envelope[ScriptChapterRow])
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
    except Exception as exc:
        logger.error(f"[Scripts] update_chapter {chapter_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update chapter")
    # ``update`` returns {} when the row is gone by the time it writes.
    return {"success": True, "data": require_row(chapter)}


@router.delete("/chapters/{chapter_id}", response_model=ScriptAck)
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


@router.post(
    "/{script_id}/canvas/sync", response_model=Envelope[ScriptCanvasSyncResult]
)
async def sync_canvas(
    auth: AuthDep, script_id: str, body: ScriptCanvasSyncRequest
) -> Dict[str, Any]:
    await verify_script_access(script_id, auth)
    await _assert_chapters_in_script(script_id, body)
    try:
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
