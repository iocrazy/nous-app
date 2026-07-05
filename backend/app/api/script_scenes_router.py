"""Script Scenes Router — scene CRUD + versioned element ops (Phase B P2).

Endpoints:
  GET  /scripts/{script_id}/scenes        — verify_script_access
  POST /scripts/{script_id}/scenes        — verify_script_access
  GET    /scenes/{scene_id}               — verify_scene_access
  PATCH  /scenes/{scene_id}               — verify_scene_access
  DELETE /scenes/{scene_id}               — verify_scene_access
  POST /scenes/{scene_id}/elements/ops    — verify_scene_access
  POST /scenes/{scene_id}/move            — verify_scene_access

The ops endpoint is the optimistic-concurrency heart: it requires an
``If-Match: <content_version>`` header (missing → 428), maps ``VersionConflict``
→ 409 and ``OpError`` → 422 with the exact body shapes the editor rebases on.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_scene_access, verify_script_access
from app.repositories.script_scene_repository import (
    UNSET,
    VersionConflict,
    get_script_scene_repository,
)
from app.schemas.script import (
    SceneCreate,
    SceneMetaUpdate,
    SceneMoveRequest,
    SceneOpsRequest,
)
from app.services.script.scene_ops import OpError

router = APIRouter()


@router.get("/scripts/{script_id}/scenes")
async def list_scenes(
    script_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """List all scenes for a script (chapter_id NULLS LAST, then sort_order)."""
    try:
        scenes = await get_script_scene_repository().list_by_script(script_id)
        return {"success": True, "data": scenes}
    except Exception as exc:
        logger.error(f"[Scenes] list for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list scenes")


@router.post("/scripts/{script_id}/scenes")
async def create_scene(
    script_id: str,
    auth: AuthDep,
    body: SceneCreate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Create a scene under a script. sort_order auto-assigns to MAX+STEP
    within the (script_id, chapter_id) group when omitted."""
    try:
        data = body.model_dump(exclude_none=True)
        data["script_id"] = script_id
        scene = await get_script_scene_repository().create(data)
        return {"success": True, "data": scene}
    except Exception as exc:
        logger.error(f"[Scenes] create for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create scene")


@router.get("/scenes/{scene_id}")
async def get_scene(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Get a single scene by id."""
    try:
        scene = await get_script_scene_repository().get_by_id(scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        return {"success": True, "data": scene}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Scenes] get {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get scene")


@router.patch("/scenes/{scene_id}")
async def update_scene(
    scene_id: str,
    auth: AuthDep,
    body: SceneMetaUpdate,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Update scene header fields / canvas coords. NEVER touches content*."""
    try:
        scene = await get_script_scene_repository().update_meta(
            scene_id, body.model_dump(exclude_none=True)
        )
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        return {"success": True, "data": scene}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Scenes] update {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update scene")


@router.delete("/scenes/{scene_id}")
async def delete_scene(
    scene_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Delete a scene (its ops cascade via FK ON DELETE CASCADE)."""
    try:
        await get_script_scene_repository().delete(scene_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Scenes] delete {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete scene")


@router.post("/scenes/{scene_id}/elements/ops")
async def apply_element_ops(
    scene_id: str,
    auth: AuthDep,
    body: SceneOpsRequest,
    if_match: Optional[str] = Header(None, alias="If-Match"),
    _guard: None = Depends(verify_scene_access),
):
    """Apply a batch of anchor-based element ops under optimistic concurrency.

    Requires ``If-Match: <content_version>``. On success returns 200 with the
    new content_version + elements; on a version race returns 409 with the
    current version + elements for the editor to rebase; on a protocol
    violation returns 422 with the OpError code.
    """
    if if_match is None:
        raise HTTPException(status_code=428, detail="If-Match header required")
    try:
        expected_version = int(if_match)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail="If-Match must be an integer content_version"
        )

    try:
        result = await get_script_scene_repository().apply_element_ops(
            scene_id,
            body.ops,
            expected_version=expected_version,
            actor=auth.user_id,
        )
        return {"success": True, "data": result}
    except VersionConflict as vc:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "code": "version_conflict",
                "current_version": vc.current_version,
                "elements": vc.elements,
            },
        )
    except OpError as oe:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "code": oe.code,
                "detail": str(oe),
            },
        )
    except Exception as exc:
        logger.error(f"[Scenes] apply ops on {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to apply element ops")


@router.post("/scenes/{scene_id}/move")
async def move_scene(
    scene_id: str,
    auth: AuthDep,
    body: SceneMoveRequest,
    _guard: None = Depends(verify_scene_access),
) -> Dict[str, Any]:
    """Reorder (and optionally reparent) a scene. ``chapter_id`` omitted keeps
    the current chapter; supplied (incl. null) reparents."""
    try:
        chapter_id = body.chapter_id if "chapter_id" in body.model_fields_set else UNSET
        scene = await get_script_scene_repository().move_scene(
            scene_id,
            chapter_id=chapter_id,
            before_scene_id=body.before_scene_id,
            after_scene_id=body.after_scene_id,
        )
        return {"success": True, "data": scene}
    except Exception as exc:
        logger.error(f"[Scenes] move {scene_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to move scene")
