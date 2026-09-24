# app/api/reviews_router.py

"""
Review system API endpoints.

Provides CRUD for review comments (with annotations) and review status.
Every route is limited to resources the caller may read
(``app/api/reviews_access.py``).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.api.reviews_access import (
    require_parent_on_resource,
    require_review_comment,
    require_review_resource,
    require_version_of_resource,
    review_not_found,
)
from app.api.row_guard import require_row
from app.core.deps import AuthDep
from app.schemas.review_responses import (
    ReviewCommentCreatedResponse,
    ReviewCommentDeleted,
    ReviewCommentListResponse,
    ReviewCommentResponse,
    ReviewStatusListResponse,
    ReviewStatusResponse,
)
from app.services.library.review_service import ReviewService

router = APIRouter(prefix="/reviews")


# ─── Request Models ─────────────────────────────────


class AnnotationInput(BaseModel):
    tool_type: str = Field(..., pattern="^(arrow|rect|freehand|text)$")
    data: Dict[str, Any]


class CreateReviewCommentRequest(BaseModel):
    resource_id: str
    version_id: Optional[str] = None
    content: str = Field(..., min_length=1, max_length=5000)
    timecode: Optional[float] = None
    frame_number: Optional[int] = None
    parent_id: Optional[str] = None
    annotations: Optional[List[AnnotationInput]] = None


class SetReviewStatusRequest(BaseModel):
    resource_id: str
    version_id: Optional[str] = None
    status: str = Field(..., pattern="^(pending|approved|needs_changes|rejected)$")
    comment: Optional[str] = None


# ─── Comment Endpoints ──────────────────────────────


@router.post("/comments", response_model=ReviewCommentCreatedResponse)
async def create_comment(body: CreateReviewCommentRequest, auth: AuthDep):
    """Create a review comment with optional annotations."""
    await require_review_resource(body.resource_id, auth.user_id)
    if body.version_id:
        await require_version_of_resource(body.version_id, body.resource_id)
    if body.parent_id:
        await require_parent_on_resource(body.parent_id, body.resource_id)
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


@router.get("/comments", response_model=ReviewCommentListResponse)
async def list_comments(
    auth: AuthDep,
    resource_id: str = Query(...),
    version_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, pattern="^(open|resolved|wontfix)$"),
):
    """List top-level comments for a resource, with replies and annotations."""
    await require_review_resource(resource_id, auth.user_id)
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


@router.post("/comments/{comment_id}/resolve", response_model=ReviewCommentResponse)
async def resolve_comment(comment_id: str, auth: AuthDep):
    """Mark a comment as resolved."""
    await require_review_comment(comment_id, auth.user_id)
    try:
        svc = ReviewService()
        comment = await svc.resolve_comment(comment_id, auth.user_id)
    except ValueError:
        raise review_not_found("Comment not found")
    except Exception as e:
        logger.error(f"Failed to resolve comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to resolve comment")
    # None: the row went away between the guard and the write.
    return {"success": True, "data": require_row(comment)}


@router.post("/comments/{comment_id}/reopen", response_model=ReviewCommentResponse)
async def reopen_comment(comment_id: str, auth: AuthDep):
    """Reopen a resolved comment."""
    await require_review_comment(comment_id, auth.user_id)
    try:
        svc = ReviewService()
        comment = await svc.reopen_comment(comment_id, auth.user_id)
    except ValueError:
        raise review_not_found("Comment not found")
    except Exception as e:
        logger.error(f"Failed to reopen comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to reopen comment")
    # None: the row went away between the guard and the write.
    return {"success": True, "data": require_row(comment)}


@router.delete("/comments/{comment_id}", response_model=ReviewCommentDeleted)
async def delete_comment(comment_id: str, auth: AuthDep):
    """Delete a comment (cascades to replies and annotations)."""
    await require_review_comment(comment_id, auth.user_id)
    try:
        svc = ReviewService()
        await svc.delete_comment(comment_id, auth.user_id)
        return {"success": True}
    except ValueError:
        raise review_not_found("Comment not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete comment")


# ─── Review Status Endpoints ────────────────────────


@router.post("/status", response_model=ReviewStatusResponse)
async def set_review_status(body: SetReviewStatusRequest, auth: AuthDep):
    """Set or update review status (approve, reject, etc.)."""
    await require_review_resource(body.resource_id, auth.user_id)
    if body.version_id:
        await require_version_of_resource(body.version_id, body.resource_id)
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


@router.get("/status", response_model=ReviewStatusListResponse)
async def get_review_statuses(
    auth: AuthDep,
    resource_id: str = Query(...),
    version_id: Optional[str] = Query(None),
):
    """Get all review statuses for a resource."""
    await require_review_resource(resource_id, auth.user_id)
    try:
        svc = ReviewService()
        statuses = await svc.get_review_statuses(resource_id, version_id)
        return {"success": True, "data": statuses}
    except Exception as e:
        logger.error(f"Failed to get review statuses: {e}")
        raise HTTPException(status_code=500, detail="Failed to get review statuses")
