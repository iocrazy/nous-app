"""Pydantic schemas for Search API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SemanticSearchRequest(BaseModel):
    """Request schema for semantic search."""

    query: str = Field(
        ..., min_length=1, max_length=500, description="Natural language search query"
    )
    limit: int = Field(20, ge=1, le=100, description="Maximum number of results")
    threshold: float = Field(
        0.5, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class HybridSearchRequest(BaseModel):
    """Request schema for hybrid search (semantic + filters)."""

    query: Optional[str] = Field(
        None, max_length=500, description="Natural language search query"
    )
    tag_ids: Optional[List[str]] = Field(None, description="Filter by tag IDs")
    author: Optional[str] = Field(
        None, max_length=100, description="Filter by author name (partial match)"
    )
    date_from: Optional[str] = Field(
        None, description="Filter by start date (ISO format)"
    )
    date_to: Optional[str] = Field(None, description="Filter by end date (ISO format)")
    limit: int = Field(20, ge=1, le=100, description="Maximum number of results")
    threshold: float = Field(
        0.4, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class SearchResultItem(BaseModel):
    """A single search result item."""

    media_id: int
    platform_id: str  # Required for frontend filtering
    title: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    similarity_score: float = Field(..., ge=0.0, le=1.0, description="Similarity score")
    tags: List[str] = Field(default_factory=list)
    author: Optional[str] = None
    view_count: int = 0
    created_at: Optional[str] = None


class SearchResponse(BaseModel):
    """Response schema for search results."""

    results: List[SearchResultItem]
    # Full ``parsed_media`` rows for every hit in ``results``, sorted by the
    # same ranking. Lets the frontend render AI-status icons / counts /
    # audio paths for search hits that are NOT already in the paginated
    # library. Without this, cards rendered minimal projections of
    # SearchResultItem and their status icons stayed empty.
    videos: List[Dict[str, Any]] = Field(default_factory=list)
    total: int
    query: str
    search_type: str  # "semantic", "hybrid", "similar"


class SimilarMediaRequest(BaseModel):
    """Request schema for finding similar media."""

    limit: int = Field(10, ge=1, le=50, description="Maximum number of similar videos")
    threshold: float = Field(
        0.6, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class SearchSuggestion(BaseModel):
    """Search suggestion for autocomplete."""

    text: str
    type: str  # "query", "tag", "author"
    count: Optional[int] = None


class SearchSuggestionsResponse(BaseModel):
    """Response schema for search suggestions."""

    suggestions: List[SearchSuggestion]
