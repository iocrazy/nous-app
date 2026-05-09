# backend/app/api/sb_canvas_router.py

"""
Storyboard Canvas Router

Endpoints for managing canvas nodes, edges, and frames:
individual CRUD, batch sync, frame metadata updates, and reorder.
"""

import mimetypes
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.storyboard_repository import (
    StoryboardEdgeRepository,
    StoryboardFrameRepository,
    StoryboardNodeRepository,
)
from app.schemas.storyboard import (
    CanvasSyncRequest,
    StoryboardFrameUpdate,
    StoryboardNodeCreate,
    StoryboardNodeUpdate,
)
from app.services.storyboard.storyboard_service import StoryboardService

router = APIRouter(prefix="/storyboard")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/nodes
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/nodes")
async def create_nodes(
    auth: AuthDep,
    project_id: str,
    body: List[StoryboardNodeCreate],
) -> Dict[str, Any]:
    """Create one or more canvas nodes in a storyboard project."""
    if not body:
        raise HTTPException(status_code=422, detail="Node list must not be empty")
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        node_repo = StoryboardNodeRepository()
        nodes = await node_repo.bulk_upsert(
            project_id,
            [n.model_dump() for n in body],
        )
        return {"success": True, "data": nodes}
    except Exception as exc:
        logger.error(f"[SBCanvas] create_nodes project={project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to create nodes: {exc}")


# ---------------------------------------------------------------------------
# PUT /nodes/{node_id}
# ---------------------------------------------------------------------------


@router.put("/nodes/{node_id}")
async def update_node(
    auth: AuthDep,
    node_id: str,
    body: StoryboardNodeUpdate,
) -> Dict[str, Any]:
    """Update position, size, or data of a single canvas node."""
    try:
        node_repo = StoryboardNodeRepository()
        updated = await node_repo.update(node_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error(f"[SBCanvas] update_node {node_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to update node: {exc}")


# ---------------------------------------------------------------------------
# DELETE /nodes/{node_id}
# ---------------------------------------------------------------------------


@router.delete("/nodes/{node_id}")
async def delete_node(auth: AuthDep, node_id: str) -> Dict[str, Any]:
    """Delete a single canvas node."""
    try:
        node_repo = StoryboardNodeRepository()
        await node_repo.delete(node_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[SBCanvas] delete_node {node_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to delete node: {exc}")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/nodes/batch
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/sync")
@router.post("/projects/{project_id}/nodes/batch")
async def sync_canvas(
    auth: AuthDep,
    project_id: str,
    body: CanvasSyncRequest,
) -> Dict[str, Any]:
    """
    Atomic incremental canvas sync.

    Applies added/updated/deleted nodes and edges in a single request.
    Returns operation counts for the caller to verify.
    """
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        result = await svc.sync_canvas(project_id, body)
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error(f"[SBCanvas] sync_canvas project={project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Canvas sync failed: {exc}")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/edges
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/edges")
async def create_edges(
    auth: AuthDep,
    project_id: str,
    body: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create one or more directed edges between canvas nodes."""
    if not body:
        raise HTTPException(status_code=422, detail="Edge list must not be empty")
    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        edge_repo = StoryboardEdgeRepository()
        edges = await edge_repo.bulk_upsert(project_id, body)
        return {"success": True, "data": edges}
    except Exception as exc:
        logger.error(f"[SBCanvas] create_edges project={project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to create edges: {exc}")


# ---------------------------------------------------------------------------
# DELETE /edges/{edge_id}
# ---------------------------------------------------------------------------


@router.delete("/edges/{edge_id}")
async def delete_edge(auth: AuthDep, edge_id: str) -> Dict[str, Any]:
    """Delete a single canvas edge."""
    try:
        edge_repo = StoryboardEdgeRepository()
        await edge_repo.delete(edge_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[SBCanvas] delete_edge {edge_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to delete edge: {exc}")


# ---------------------------------------------------------------------------
# GET /nodes/{node_id}/frames
# ---------------------------------------------------------------------------


@router.get("/nodes/{node_id}/frames")
async def get_frames(
    auth: AuthDep,
    node_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """Return paginated frames for a canvas node."""
    try:
        frame_repo = StoryboardFrameRepository()
        all_frames = await frame_repo.get_by_node(node_id)
        total = len(all_frames)
        offset = (page - 1) * limit
        page_frames = all_frames[offset : offset + limit]
        return {
            "success": True,
            "data": {
                "items": page_frames,
                "total": total,
                "page": page,
                "limit": limit,
            },
        }
    except Exception as exc:
        logger.error(f"[SBCanvas] get_frames node={node_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get frames: {exc}")


# ---------------------------------------------------------------------------
# PUT /frames/{frame_id}
# ---------------------------------------------------------------------------


@router.put("/frames/{frame_id}")
async def update_frame(
    auth: AuthDep,
    frame_id: str,
    body: StoryboardFrameUpdate,
) -> Dict[str, Any]:
    """Update cinematography metadata on a storyboard frame."""
    try:
        frame_repo = StoryboardFrameRepository()
        updated = await frame_repo.update(frame_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error(f"[SBCanvas] update_frame {frame_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to update frame: {exc}")


# ---------------------------------------------------------------------------
# POST /frames/reorder
# ---------------------------------------------------------------------------


@router.post("/frames/reorder")
async def reorder_frames(
    auth: AuthDep,
    body: List[str],
) -> Dict[str, Any]:
    """
    Reorder frames by providing an ordered list of frame UUIDs.

    The list must contain at least one ID and defines the new order_index
    sequence for those frames.
    """
    if not body:
        raise HTTPException(status_code=422, detail="Frame ID list must not be empty")
    try:
        frame_repo = StoryboardFrameRepository()
        await frame_repo.reorder(body)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[SBCanvas] reorder_frames failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to reorder frames: {exc}")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/split-image
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/split-image")
async def split_image(
    auth: AuthDep,
    project_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Split an uploaded image asset into a grid of individual frames.

    Request body:
        asset_id (str): ID of the source storyboard asset.
        rows (int): Number of rows in the grid (1–10).
        cols (int): Number of columns in the grid (1–10).
        node_id (str, optional): Canvas node ID to attach the frames to.

    Returns:
        Dict with frames list, source_asset_id, rows, cols.
    """
    asset_id = body.get("asset_id")
    rows = body.get("rows")
    cols = body.get("cols")
    node_id = body.get("node_id")

    if not asset_id:
        raise HTTPException(status_code=422, detail="asset_id is required")
    if not isinstance(rows, int) or not isinstance(cols, int):
        raise HTTPException(status_code=422, detail="rows and cols must be integers")
    if rows < 1 or rows > 10 or cols < 1 or cols > 10:
        raise HTTPException(
            status_code=422,
            detail="rows and cols must be between 1 and 10",
        )

    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        result = await svc.split_image_asset(
            project_id=project_id,
            asset_id=str(asset_id),
            rows=rows,
            cols=cols,
            node_id=str(node_id) if node_id else None,
        )
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "[SBCanvas] split_image project=%s asset=%s failed: %s",
            project_id,
            asset_id,
            exc,
        )
        raise HTTPException(status_code=500, detail=f"Failed to split image: {exc}")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/upload
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


@router.post("/projects/{project_id}/upload")
async def upload_image(
    auth: AuthDep,
    project_id: str,
    file: UploadFile = File(...),
    node_id: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """
    Upload an image to a storyboard project.

    Handles deduplication via SHA-256 hash. If the same file already exists
    in the project, returns the existing asset record without re-saving.
    A 512px preview thumbnail is generated automatically.
    """
    # Validate content type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported image type: {content_type}. "
            f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    try:
        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)

        # Read file bytes (with size limit)
        file_bytes = await file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB",
            )
        if not file_bytes:
            raise HTTPException(status_code=422, detail="Empty file")

        result = await svc.upload_image(
            project_id=project_id,
            file_bytes=file_bytes,
            filename=file.filename or "image.png",
            content_type=content_type,
            node_id=node_id,
        )
        return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[SBCanvas] upload_image project={project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to upload image: {exc}")


# ---------------------------------------------------------------------------
# GET /assets/{asset_id}/file
# ---------------------------------------------------------------------------


@router.get("/assets/{asset_id}/file")
async def serve_asset_file(
    auth: AuthDep,
    asset_id: str,
    preview: bool = Query(
        False, description="Return preview thumbnail instead of original"
    ),
) -> FileResponse:
    """
    Serve a storyboard asset file (original or preview thumbnail).

    The caller must be a member of the team that owns the asset's project.
    """
    try:
        svc = StoryboardService()
        asset = await svc.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        # Verify access via project
        await svc.verify_project_access(str(asset["project_id"]), auth.user_id)

        import os
        from pathlib import Path

        nas_base = os.environ.get("NAS_BASE_PATH", "/app/downloads")

        if preview and asset.get("preview_path"):
            file_path = Path(nas_base) / asset["preview_path"]
        else:
            file_path = Path(nas_base) / asset["file_path"]

        file_path = file_path.resolve()

        # Security: prevent path traversal
        nas_base_resolved = Path(nas_base).resolve()
        if not str(file_path).startswith(str(nas_base_resolved)):
            raise HTTPException(status_code=403, detail="Access denied")

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found on disk")

        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return FileResponse(str(file_path), media_type=mime)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[SBCanvas] serve_asset_file {asset_id} failed: {exc}")
        raise HTTPException(
            status_code=500, detail=f"Failed to serve asset file: {exc}"
        )
