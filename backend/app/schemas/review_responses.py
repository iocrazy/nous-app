"""Response shapes of ``/api/v1/reviews`` (``app/api/reviews_router.py``).

Every model mirrors the dict the route already built, key for key (spec
2026-09-24-openapi-typed-frontend-design.md §5; wire tests in
``tests/api/test_reviews_wire.py``). The rows come from
``app/repositories/review_repository.py``, whose ``_parity`` has already
turned uuids into strings and datetimes into ``isoformat()`` strings, so
timestamps are declared ``str`` (they keep the ``+00:00`` form). BIGINT ids
stay JSON numbers.

The share-visitor view of the same ``review_comments`` rows is
``ShareComment`` / ``ShareCommentRow`` in ``share_responses.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.envelope import Envelope


class ReviewAnnotationRow(BaseModel):
    """One ``review_annotations`` row (a drawing attached to a comment)."""

    id: int
    comment_id: int
    tool_type: str
    data: dict[str, Any]
    created_at: str


class ReviewCommentRow(BaseModel):
    """One ``review_comments`` row as the repository returns it."""

    id: int
    resource_id: int
    author_id: str
    content: str
    status: str
    created_at: str
    updated_at: str
    timecode: float | None
    frame_number: int | None
    parent_id: int | None
    version_id: int | None


class ReviewCommentWithAnnotations(ReviewCommentRow):
    """A new comment (``POST /comments``) or a reply inside a thread."""

    annotations: list[ReviewAnnotationRow]


class ReviewCommentThread(ReviewCommentWithAnnotations):
    """A top-level comment from ``GET /comments``, with its replies."""

    replies: list[ReviewCommentWithAnnotations]


class ReviewStatusRow(BaseModel):
    """One ``review_status`` row (a reviewer's verdict on a resource)."""

    id: int
    resource_id: int
    reviewer_id: str
    status: str
    created_at: str
    updated_at: str
    comment: str | None
    version_id: int | None


class ReviewCommentDeleted(BaseModel):
    """``DELETE /comments/{id}`` sends no ``data`` key."""

    success: bool = True


ReviewCommentCreatedResponse = Envelope[ReviewCommentWithAnnotations]
ReviewCommentListResponse = Envelope[list[ReviewCommentThread]]
ReviewCommentResponse = Envelope[ReviewCommentRow]
ReviewStatusResponse = Envelope[ReviewStatusRow]
ReviewStatusListResponse = Envelope[list[ReviewStatusRow]]
