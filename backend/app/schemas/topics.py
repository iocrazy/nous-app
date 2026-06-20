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


class HotspotListResponse(BaseModel):
    success: bool = True
    count: int
    hotspots: list[HotspotOut]


class DatesResponse(BaseModel):
    success: bool = True
    dates: list[str]
