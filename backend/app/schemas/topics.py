from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class HotspotOut(BaseModel):
    id: str
    title: str
    url: Optional[str] = None
    origin_url: Optional[str] = None
    source_label: Optional[str] = None
    summary: Optional[str] = None
    ai_summary: Optional[str] = None
    reason: Optional[str] = None
    score: Optional[float] = None
    tags: list[str] = []
    category: Optional[str] = None
    media_url: Optional[str] = None
    cover_url: Optional[str] = None
    captured_at: Optional[str] = None
    # Full original/translated body — only populated by the detail endpoint to
    # keep the list payload light.
    content_original: Optional[str] = None
    content_translated: Optional[str] = None
    # Per-user state (merged from hotspot_user_state; all false when no row).
    is_read: bool = False
    is_saved: bool = False
    is_hidden: bool = False


class HotspotListResponse(BaseModel):
    success: bool = True
    count: int
    hotspots: list[HotspotOut]


class HotspotDetailResponse(BaseModel):
    success: bool = True
    hotspot: HotspotOut


class HotspotStateRequest(BaseModel):
    is_read: Optional[bool] = None
    is_saved: Optional[bool] = None
    is_hidden: Optional[bool] = None


class HotspotStateResponse(BaseModel):
    success: bool = True
    is_read: bool = False
    is_saved: bool = False
    is_hidden: bool = False


class DatesResponse(BaseModel):
    success: bool = True
    dates: list[str]


class SourceHealthOut(BaseModel):
    id: str
    name: str
    kind: str
    category: Optional[str] = None
    enabled: bool = True
    health: str = "ok"  # ok | degraded | dead
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_fetched_at: Optional[str] = None
    last_ok_at: Optional[str] = None


class SourceHealthResponse(BaseModel):
    success: bool = True
    count: int
    sources: list[SourceHealthOut]
