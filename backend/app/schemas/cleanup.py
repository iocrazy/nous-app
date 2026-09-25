"""Pydantic schemas for Cleanup Suggestions API."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel


class CleanupSuggestion(BaseModel):
    """A single cleanup suggestion."""

    media_id: int
    title: str
    cover_url: Optional[str]
    author: Optional[str]
    reason: str  # "never_viewed", "duplicate_content", "old_unused", "large_file"
    reason_detail: str
    storage_size: Optional[int]  # bytes
    created_at: datetime
    last_viewed_at: Optional[datetime]
    view_count: int
    similarity_to: Optional[int] = None  # media_id if duplicate
    similarity_score: Optional[float] = None


class CleanupSuggestionsResponse(BaseModel):
    """Response schema for cleanup suggestions."""

    suggestions: List[CleanupSuggestion]
    total_count: int
    total_reclaimable_bytes: int
    categories: dict  # count per reason


class CleanupAction(BaseModel):
    """Action to take on a cleanup suggestion."""

    action: Literal["delete", "keep_forever", "dismiss"]


class CleanupBatchAction(BaseModel):
    """Batch action on multiple suggestions."""

    media_ids: List[int]
    action: Literal["delete", "keep_forever", "dismiss"]


class CleanupStats(BaseModel):
    """Overall cleanup statistics."""

    total_videos: int
    total_storage_bytes: int
    videos_never_viewed: int
    videos_not_viewed_30_days: int
    potential_duplicates: int
    videos_marked_keep: int
    reclaimable_bytes: int


class CleanupMediaActionResult(BaseModel):
    """``POST /cleanup/media/{id}/action`` and ``POST|DELETE .../keep``."""

    message: str
    media_id: int


class CleanupBatchResult(BaseModel):
    """``POST /cleanup/batch``: per-id outcome counts; ``failed_ids`` are the
    media ids the caller owns no resource for (or whose action raised)."""

    message: str
    action: Literal["delete", "keep_forever", "dismiss"]
    success_count: int
    failed_count: int
    failed_ids: List[int]


class CleanupStorageByType(BaseModel):
    """Bytes per bucket: ``video`` (video/special), ``image`` (carousel /
    image_text), ``other`` (everything else, including a NULL media_type)."""

    video: int
    image: int
    other: int


class CleanupStorageMonth(BaseModel):
    month: str  # YYYY-MM
    count: int
    bytes: int


class CleanupStorageItem(BaseModel):
    """One of the ten largest owned media (``parsed_media`` columns)."""

    id: int
    storage_size: int
    media_type: Optional[str]
    created_at: Optional[str]  # ISO string (``isoformat()`` in the route)


class CleanupStorageBreakdown(BaseModel):
    """``GET /cleanup/storage``: storage over the caller's non-trashed media."""

    by_type: CleanupStorageByType
    by_month: List[CleanupStorageMonth]
    largest_videos: List[CleanupStorageItem]
    total_bytes: int
    total_videos: int


class CleanupDataResponse(BaseModel):
    """Combined response with suggestions, stats, and categories in one call."""

    suggestions: List[CleanupSuggestion]
    total_count: int
    total_reclaimable_bytes: int
    categories: dict
    stats: CleanupStats
