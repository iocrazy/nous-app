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

# Which retrieval leg produced a hit (``SearchResultItem.layer``). Mirrored by
# ``HitLayer`` in frontend/services/searchService.ts, which keys the hit
# badges on it; ``tests/test_search_fields_frontend_mirror.py`` pins the two.
HitLayer = Literal["text", "semantic", "visual", "camera", "transcript"]

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
    #: Same chips as /search/text — Smart Search runs through the same RPC, so
    #: they apply identically. See LibraryChipFilters below.
    filters: Optional["LibraryChipFilters"] = None


class LibraryChipFilters(BaseModel):
    """The My Downloads filter chips, in the shape the list path already uses.

    These exist because the list and the search were two different queries and
    only the list applied them. Ticking "AI · transcribed" and then typing a
    keyword silently returned untranscribed rows, with the chip still drawn as
    active. Field names mirror ``FetchLibraryFilterParams`` on the frontend and
    the ``p_*`` arguments of ``rpc_downloads_library_search``, so all three can
    be read side by side.

    Every field is optional and ``None`` means "no opinion". ``False`` on a
    boolean means the same — a chip that is off must not become a filter for
    the opposite, which is what a naive ``if flag is not None`` would do.
    """

    tag_ids: Optional[List[str]] = None
    min_rating: Optional[int] = None
    ai_transcribed: Optional[bool] = None
    ai_summarized: Optional[bool] = None
    ai_analyzed: Optional[bool] = None
    ai_has_prompt: Optional[bool] = None
    created_after: Optional[str] = None
    created_before: Optional[str] = None
    duration_min: Optional[int] = None
    duration_max: Optional[int] = None
    aspect_ratios: Optional[List[str]] = None
    platforms: Optional[List[str]] = None
    media_types: Optional[List[str]] = None
    has_comments: Optional[bool] = None
    min_likes: Optional[int] = None
    min_comments: Optional[int] = None
    min_favorites: Optional[int] = None
    min_shares: Optional[int] = None
    social_combine: Optional[Literal["and", "or"]] = None


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
    #: The caller's active filter chips. Omitted by callers that have none;
    #: when present they narrow the hits exactly as they narrow the unsearched
    #: list, because both end up in the same SQL predicates (migration 475).
    filters: Optional[LibraryChipFilters] = None


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
    #: Which leg produced the hit: "text" (ILIKE) or "semantic" (vector)
    #: today; the other members are reserved for the shot / transcript legs.
    layer: HitLayer = "text"


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
    # Hybrid only — what the vector leg did: one of
    # ``search_service.VECTOR_LEG_OUTCOMES`` ("ok" | "unconfigured" |
    # "embed_failed" | "dimension_mismatch" | "timeout" | "store_missing" |
    # "error" | "skipped_filters" | "skipped_no_scope" | "skipped_no_query" |
    # "skipped_full_page"; "unavailable" is a legacy spelling no longer
    # emitted). Absent on other search types. Lets a UI say "keyword results
    # only" and a probe fire when the leg degrades.
    vector_leg: Optional[str] = None
    #: Hybrid only — how many of ``results`` each leg contributed after the
    #: merge (``{"text": n, "semantic": m}``).
    legs: Optional[Dict[str, int]] = None
    #: Whether a reranker reordered the merged list (none yet: always false).
    reranked: bool = False


class SpaceInfo(BaseModel):
    """The embedding space vectors are written to and searched in.

    ``id`` is a Snowflake BIGINT serialised as a STRING: JS loses precision
    past 2^53 (CLAUDE.md "Snowflake BIGINT 精度丢失"), and this id is only
    ever echoed back, never computed on.
    """

    id: str
    actual_model: str
    protocol: str
    dims: int
    modalities: List[str]
    instruction_version: str

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "SpaceInfo":
        """From an ``embedding_spaces`` row dict (int id, extra columns)."""
        return cls(**{**{k: row[k] for k in cls.model_fields}, "id": str(row["id"])})


class LayerStatus(BaseModel):
    """Coverage of one retrieval layer for the caller, in the current space."""

    layer: Literal["semantic", "transcript"]
    status: Literal["ok", "not_built"]
    covered: int
    total: int
    #: Vectors in ``covered`` that the next backfill would re-embed (written
    #: by another DOC_VERSION, or older than a summary / transcript).
    stale: int = 0


class SpaceStatus(SpaceInfo):
    """One embedding space and the caller's coverage in it.

    ``active`` = the admin governance embedder writes and searches here;
    every other space is a candidate. ``catalog_name`` is the ``nous_models``
    row serving ``actual_model`` (null when it was removed from the catalog:
    such a space can neither be filled nor switched to).
    """

    active: bool
    catalog_name: Optional[str] = None
    layers: List[LayerStatus]


class VectorsStatusResponse(BaseModel):
    """``GET /search/vectors/status``.

    ``status``: "ok" (space resolved, coverage counted) | "unconfigured" (no
    embedding model; ``space`` null, coverage 0 of the caller's total) |
    "store_missing" (migration 499 not applied; ``space`` null, ``layers``
    empty). A layer is "not_built" until it holds at least one vector;
    ``transcript`` is always "not_built" until that layer ships.

    ``space`` / ``layers`` describe the ACTIVE space (kept for old readers);
    ``spaces`` lists every space, active and candidates, each with the
    caller's coverage. ``can_manage`` = the caller may add / switch / delete
    spaces (admin).
    """

    space: Optional[SpaceInfo] = None
    status: Literal["ok", "unconfigured", "store_missing"]
    layers: List[LayerStatus]
    spaces: List[SpaceStatus] = Field(default_factory=list)
    can_manage: bool = False


class CreateSpaceRequest(BaseModel):
    """``POST /search/vectors/spaces``: a ``nous_models`` embedding row name."""

    model_name: str = Field(..., min_length=1, max_length=200)


class DeleteSpaceResponse(BaseModel):
    """``DELETE /search/vectors/spaces/{id}``. ``deleted_vectors`` = rows of
    ``resource_embeddings`` (every user, every layer) the FK cascade removed."""

    deleted: bool
    space_id: str
    deleted_vectors: int


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


# HybridSearchRequest references LibraryChipFilters before it is defined.
HybridSearchRequest.model_rebuild()
