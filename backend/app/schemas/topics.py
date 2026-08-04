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
    # Objective popularity (0..1) from board rank + persistence, and the best
    # board position ever held — for ranking and display.
    heat: Optional[float] = None
    best_rank: Optional[int] = None
    # Cross-source cluster size: how many distinct platforms this topic appears
    # on (from its topic_group). >1 = "seen on N platforms" signal.
    source_count: Optional[int] = None
    # The distinct member source labels behind source_count (e.g. ["Hacker News
    # 热门", "微博热搜"]) — powers the "which platforms" hover tooltip. Empty
    # when the topic isn't clustered or sits on a single source.
    source_names: list[str] = []
    # Raw per-dimension scores (novelty/impact/credibility/actionability/
    # shareability, 0..1) the LLM rated — the breakdown behind ``score``. Only
    # on the detail endpoint (transparency into why an item scored what it did).
    score_dims: Optional[dict] = None
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


class InterestRequest(BaseModel):
    interest_text: str = ""


class InterestResponse(BaseModel):
    success: bool = True
    interest_text: str = ""
    # True once the interest has an embedding (the "For You" view needs it).
    has_embedding: bool = False


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
    # is_owner: this caller created the source (can delete it). System sources
    # (user_id NULL) are is_owner=False — deletable by nobody, only hidable.
    is_owner: bool = False
    # is_hidden: this caller has "closed" the source (excluded from their feed).
    is_hidden: bool = False


class SourceHealthResponse(BaseModel):
    success: bool = True
    count: int
    sources: list[SourceHealthOut]


class SourceCreateRequest(BaseModel):
    kind: str  # newsnow | rss | http_api | custom
    name: str
    category: Optional[str] = None
    config: dict = {}


class SourceMutationResponse(BaseModel):
    success: bool = True
    source: Optional[SourceHealthOut] = None
