"""Pydantic schemas for Cleanup Suggestions API."""
from datetime import datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, Field


class CleanupSuggestion(BaseModel):
    """A single cleanup suggestion."""
    video_id: int
    title: str
    cover_url: Optional[str]
    author: Optional[str]
    reason: str  # "never_viewed", "duplicate_content", "old_unused", "large_file"
    reason_detail: str
    storage_size: Optional[int]  # bytes
    created_at: datetime
    last_viewed_at: Optional[datetime]
    view_count: int
    similarity_to: Optional[int] = None  # video_id if duplicate
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
    video_ids: List[int]
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


class StorageBreakdown(BaseModel):
    """Storage usage breakdown."""
    by_type: dict  # video, image, audio
    by_month: List[dict]  # [{month, count, bytes}]
    by_tag: List[dict]  # [{tag, count, bytes}]
    largest_videos: List[dict]  # top 10 by size


class CleanupDataResponse(BaseModel):
    """Combined response with suggestions, stats, and categories in one call."""
    suggestions: List[CleanupSuggestion]
    total_count: int
    total_reclaimable_bytes: int
    categories: dict
    stats: CleanupStats
