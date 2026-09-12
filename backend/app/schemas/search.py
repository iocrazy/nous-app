"""Pydantic schemas for Search API."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SearchField = Literal[
    "title",
    "description",
    "author",
    "hashtags",
    # Eagle-style extras — different storage layout than the four above:
    "transcript",  # resource_transcripts.full_text + parsed_media.ai_extract_text
    "tags",  # tags.name → resource_tags → resources.media_id (join chain)
    "notes",  # resources.notes (per-user)
]

# The search box placeholder promises "title, tags, notes", so all three have
# to be on by default — shipping ``tags``/``notes`` as opt-in meant a user who
# never opened the scope picker could not find anything by tag (927 of this
# repo's pilot user's 1383 downloads carry tags, none were reachable).
# ``transcript`` stays opt-in: it is the only genuinely heavy scope (a join
# against multi-KB transcript bodies) and the placeholder never promised it.
DEFAULT_SEARCH_FIELDS: List[SearchField] = [
    "title",
    "description",
    "author",
    "hashtags",
    "tags",
    "notes",
]


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
    fields: List[SearchField] = Field(
        default_factory=lambda: list(DEFAULT_SEARCH_FIELDS),
        description="Which scopes to search; defaults to the shared default set",
    )


class TextSearchRequest(BaseModel):
    """Request schema for plain-text ILIKE search (no semantic ranking).

    Returns matches whose selected ``fields`` contain the substring,
    sorted by ``created_at DESC``. Use this when the user wants
    "everything that contains this keyword" rather than top-N semantic.

    ``fields`` controls which scopes to search. When omitted it falls back to
    ``DEFAULT_SEARCH_FIELDS`` above — do not restate the list here, the two
    copies would drift. The scopes that are not plain ``parsed_media`` columns:
      * ``transcript`` — ``resource_transcripts.full_text``, plus the legacy
        ``parsed_media.ai_extract_text`` column so pre-463 rows stay reachable
        (heavyweight: transcript bodies run to tens of KB).
      * ``tags`` — joins through ``resource_tags`` → ``tags.name``
      * ``notes`` — searches ``resources.notes`` for the current user
    An empty list returns no results (caller probably means "no scopes
    selected" rather than "any scope" — fail closed). ``/search/hybrid``
    reads an empty list the same way.
    """

    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(1000, ge=1, le=5000)
    fields: List[SearchField] = Field(
        default_factory=lambda: list(DEFAULT_SEARCH_FIELDS)
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
