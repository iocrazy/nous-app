# app/api/reviews_router.py

"""
Review system API endpoints.

Provides CRUD for review comments (with annotations) and review status.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.services.review_service import ReviewService

router = APIRouter(prefix="/reviews")


# ─── Request Models ─────────────────────────────────

class AnnotationInput(BaseModel):
    tool_type: str = Field(..., pattern="^(arrow|rect|freehand|text)$")
    data: Dict[str, Any]


class CreateCommentRequest(BaseModel):
    resource_id: str
    version_id: Optional[str] = None
    content: str = Field(..., min_length=1, max_length=5000)
    timecode: Optional[float] = None
    frame_number: Optional[int] = None
    parent_id: Optional[str] = None
    annotations: Optional[List[AnnotationInput]] = None


class UpdateCommentRequest(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=5000)
    status: Optional[str] = Field(None, pattern="^(open|resolved|wontfix)$")


class SetReviewStatusRequest(BaseModel):
    resource_id: str
    version_id: Optional[str] = None
    status: str = Field(..., pattern="^(pending|approved|needs_changes|rejected)$")
    comment: Optional[str] = None


# ─── Comment Endpoints ──────────────────────────────

@router.post("/comments")
async def create_comment(body: CreateCommentRequest, auth: AuthDep):
    """Create a review comment with optional annotations."""
    try:
        svc = ReviewService()
        annotations = (
            [a.model_dump() for a in body.annotations] if body.annotations else None
        )
        comment = await svc.create_comment(
            resource_id=body.resource_id,
            author_id=auth.user_id,
            content=body.content,
            version_id=body.version_id,
            timecode=body.timecode,
            frame_number=body.frame_number,
            parent_id=body.parent_id,
            annotations=annotations,
        )
        return {"success": True, "data": comment}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to create comment")


@router.get("/comments")
async def list_comments(
    auth: AuthDep,
    resource_id: str = Query(...),
    version_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, pattern="^(open|resolved|wontfix)$"),
):
    """List top-level comments for a resource, with replies and annotations."""
    try:
        svc = ReviewService()
        comments = await svc.get_comments(
            resource_id=resource_id,
            version_id=version_id,
            status=status,
        )
        return {"success": True, "data": comments}
    except Exception as e:
        logger.error(f"Failed to list comments: {e}")
        raise HTTPException(status_code=500, detail="Failed to list comments")


@router.get("/comments/{comment_id}")
async def get_comment(comment_id: str, auth: AuthDep):
    """Get a single comment with replies and annotations."""
    try:
        svc = ReviewService()
        comment = await svc.get_comment(comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        return {"success": True, "data": comment}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to get comment")


@router.patch("/comments/{comment_id}")
async def update_comment(
    comment_id: str, body: UpdateCommentRequest, auth: AuthDep
):
    """Update a comment's content or status."""
    try:
        svc = ReviewService()
        updates = body.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        comment = await svc.update_comment(comment_id, auth.user_id, updates)
        return {"success": True, "data": comment}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to update comment")


@router.post("/comments/{comment_id}/resolve")
async def resolve_comment(comment_id: str, auth: AuthDep):
    """Mark a comment as resolved."""
    try:
        svc = ReviewService()
        comment = await svc.resolve_comment(comment_id, auth.user_id)
        return {"success": True, "data": comment}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to resolve comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to resolve comment")


@router.post("/comments/{comment_id}/reopen")
async def reopen_comment(comment_id: str, auth: AuthDep):
    """Reopen a resolved comment."""
    try:
        svc = ReviewService()
        comment = await svc.reopen_comment(comment_id, auth.user_id)
        return {"success": True, "data": comment}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to reopen comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to reopen comment")


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, auth: AuthDep):
    """Delete a comment (cascades to replies and annotations)."""
    try:
        svc = ReviewService()
        await svc.delete_comment(comment_id, auth.user_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete comment")


@router.get("/comments/count")
async def get_comment_count(
    auth: AuthDep,
    resource_id: str = Query(...),
    version_id: Optional[str] = Query(None),
):
    """Get the number of top-level comments for a resource."""
    try:
        svc = ReviewService()
        count = await svc.get_comment_count(resource_id, version_id)
        return {"success": True, "data": {"count": count}}
    except Exception as e:
        logger.error(f"Failed to get comment count: {e}")
        raise HTTPException(status_code=500, detail="Failed to get comment count")


# ─── Review Status Endpoints ────────────────────────

@router.post("/status")
async def set_review_status(body: SetReviewStatusRequest, auth: AuthDep):
    """Set or update review status (approve, reject, etc.)."""
    try:
        svc = ReviewService()
        status = await svc.set_review_status(
            resource_id=body.resource_id,
            reviewer_id=auth.user_id,
            status=body.status,
            version_id=body.version_id,
            comment=body.comment,
        )
        return {"success": True, "data": status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to set review status: {e}")
        raise HTTPException(status_code=500, detail="Failed to set review status")


@router.get("/status")
async def get_review_statuses(
    auth: AuthDep,
    resource_id: str = Query(...),
    version_id: Optional[str] = Query(None),
):
    """Get all review statuses for a resource."""
    try:
        svc = ReviewService()
        statuses = await svc.get_review_statuses(resource_id, version_id)
        return {"success": True, "data": statuses}
    except Exception as e:
        logger.error(f"Failed to get review statuses: {e}")
        raise HTTPException(status_code=500, detail="Failed to get review statuses")
