"""Beat Templates Router — user custom beat-sheet template CRUD (Beats M3.5).

Endpoints:
  GET    /beat-templates                 — list the caller's templates (own only)
  POST   /beat-templates                 — create (owner = caller)
  PUT    /beat-templates/{template_id}   — rename (verify_beat_template_access)
  DELETE /beat-templates/{template_id}   — delete (verify_beat_template_access)

A custom template is a private per-user artifact: a name plus an ordered list of
percentage anchors carrying each beat's own title / summary / color. Pure
synchronous CRUD — no AI, no workflow. Ownership is enforced by the caller
(list/create scope to auth.user_id) and by the guard on the id-scoped routes.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_beat_template_access
from app.repositories.beat_template_repository import get_beat_template_repository
from app.schemas.script import BeatTemplateCreate, BeatTemplateRename

router = APIRouter()


@router.get("/beat-templates")
async def list_beat_templates(auth: AuthDep) -> Dict[str, Any]:
    """List the caller's custom templates, newest first."""
    try:
        templates = await get_beat_template_repository().list_by_user(auth.user_id)
        return {"success": True, "data": templates}
    except Exception as exc:
        logger.error(f"[BeatTemplates] list for user {auth.user_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list beat templates")


@router.post("/beat-templates")
async def create_beat_template(
    auth: AuthDep, body: BeatTemplateCreate
) -> Dict[str, Any]:
    """Create a custom template owned by the caller. ``user_id`` comes from the
    auth context, never the body."""
    try:
        anchors = [a.model_dump() for a in body.anchors]
        template = await get_beat_template_repository().create(
            auth.user_id, body.name, anchors
        )
        return {"success": True, "data": template}
    except Exception as exc:
        logger.error(f"[BeatTemplates] create for user {auth.user_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create beat template")


@router.put("/beat-templates/{template_id}")
async def rename_beat_template(
    template_id: str,
    auth: AuthDep,
    body: BeatTemplateRename,
    _guard: None = Depends(verify_beat_template_access),
) -> Dict[str, Any]:
    """Rename a custom template (owner only)."""
    try:
        template = await get_beat_template_repository().rename(template_id, body.name)
        if template is None:
            raise HTTPException(status_code=404, detail="Beat template not found")
        return {"success": True, "data": template}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[BeatTemplates] rename {template_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to rename beat template")


@router.delete("/beat-templates/{template_id}")
async def delete_beat_template(
    template_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_beat_template_access),
) -> Dict[str, Any]:
    """Delete a custom template (owner only)."""
    try:
        await get_beat_template_repository().delete(template_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[BeatTemplates] delete {template_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete beat template")
