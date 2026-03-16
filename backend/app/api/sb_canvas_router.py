# backend/app/api/sb_canvas_router.py

"""
Storyboard Canvas Router

Endpoints for managing canvas nodes, edges, and frames:
individual CRUD, batch sync, frame metadata updates, and reorder.
"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.storyboard_repository import (
    StoryboardNodeRepository,
    StoryboardEdgeRepository,
    StoryboardFrameRepository,
)
from app.schemas.storyboard import (
    CanvasSyncRequest,
    StoryboardFrameUpdate,
    StoryboardNodeCreate,
    StoryboardNodeUpdate,
)
from app.services.storyboard_service import StoryboardService

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
        logger.error("[SBCanvas] create_nodes project=%s failed: %s", project_id, exc)
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
        logger.error("[SBCanvas] update_node %s failed: %s", node_id, exc)
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
        logger.error("[SBCanvas] delete_node %s failed: %s", node_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to delete node: {exc}")


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/nodes/batch
# ---------------------------------------------------------------------------


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
        logger.error("[SBCanvas] sync_canvas project=%s failed: %s", project_id, exc)
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
        logger.error("[SBCanvas] create_edges project=%s failed: %s", project_id, exc)
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
        logger.error("[SBCanvas] delete_edge %s failed: %s", edge_id, exc)
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
        logger.error("[SBCanvas] get_frames node=%s failed: %s", node_id, exc)
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
        logger.error("[SBCanvas] update_frame %s failed: %s", frame_id, exc)
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
        logger.error("[SBCanvas] reorder_frames failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to reorder frames: {exc}")
