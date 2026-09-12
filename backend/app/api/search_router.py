"""API routes for Semantic Search."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.analysis_repository import EmbeddingSearchUnavailable
from app.schemas.search import (
    HybridSearchRequest,
    SearchResponse,
    SearchResultItem,
    SemanticSearchRequest,
    TextSearchRequest,
)
from app.services.library.like_escape import escape_like
from app.services.library.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Search"])


async def _fetch_user_resources_by_media_id(
    user_id: str,
    media_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Return a ``{media_id: {resource_id, ai_status_fields}}`` map.

    Search hydration needs more than ``parsed_media`` columns:
      * ``resource_id`` so the card click can navigate to the right
        ``/resources/file/<id>`` URL (search hits without it would
        fall back to ``parsed_media.id`` and 404 on detail load).
      * AI status fields (``transcript_status`` / ``summary_status`` /
        ``visual_analysis_status``) so the card's AI dot icons reflect
        the per-user processing state instead of always rendering grey.
      * ``has_prompt`` — same signal the library list overlays, so the
        card's Prompt icon lights on search hits too. Computed in SQL
        (``has_prompt_expr``) rather than selecting the prompt text: the
        columns are capped at 20 000 chars each and would dominate the
        payload for a page of hits.

    Empty ``media_ids`` short-circuits to avoid an unnecessary query.
    """
    if not media_ids:
        return {}
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Resources
    from app.repositories._orm_helpers import _plain
    from app.repositories.media_repository import has_prompt_expr

    async with read_scope() as session:
        result = await session.execute(
            select(
                Resources.id,
                Resources.media_id,
                Resources.transcript_status,
                Resources.summary_status,
                Resources.visual_analysis_status,
                has_prompt_expr().label("has_prompt"),
            )
            .where(Resources.creator_id == user_id)
            .where(Resources.source_type == "web")
            .where(Resources.is_trashed.is_(False))
            .where(Resources.media_id.in_([int(m) for m in media_ids]))
        )
        rows = result.mappings().all()
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        mid = row.get("media_id")
        if mid is None:
            continue
        out[int(mid)] = {
            "resource_id": str(row["id"]) if row.get("id") is not None else None,
            "transcript_status": _plain(row.get("transcript_status")),
            "summary_status": _plain(row.get("summary_status")),
            "visual_analysis_status": _plain(row.get("visual_analysis_status")),
            "has_prompt": bool(row.get("has_prompt")),
        }
    return out


async def _hydrate_media_by_platform_ids(
    platform_ids: List[str],
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch card-view parsed_media rows for a list of platform_ids.

    Search endpoints return slim ``SearchResultItem`` objects. The UI also
    needs a richer card-view projection (AI status, audio paths, like/
    comment/share/favorite counts, hashtags, etc.) to render the same card
    chrome on search hits as on regular library rows. This helper pulls that
    projection in a single ``IN`` query and re-sorts to match the ranking.

    Heavy AI text fields (``ai_extract_text`` / ``ai_rewrite_text`` /
    ``ai_analyze_text``) are excluded — the detail endpoint is the one that
    returns them. See MediaRepository.CARD_SELECT for the field list.

    When ``user_id`` is provided, the hydration is scoped to rows the user
    actually owns. Scale Tier-1c: ownership is resolved by intersecting the
    (small, top-N ranker output) ``platform_ids`` against the user's
    ``resources`` via the ``rpc_user_owned_platform_ids`` RPC — bounded by the
    input array, so no 1000-row-capped allowlist fetch and no URL blow-up.

    Returns empty list if ``platform_ids`` is empty.
    """
    if not platform_ids:
        return []
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ParsedMedia
    from app.repositories.media_repository import _pm_card_dict

    async with read_scope() as session:
        result = await session.execute(
            select(ParsedMedia).where(ParsedMedia.platform_id.in_(platform_ids))
        )
        rows = [_pm_card_dict(obj) for obj in result.scalars().all()]
    # Scope to rows the user owns. The ranker's ``platform_ids`` is already
    # small, so the ownership intersection is bounded — no full allowlist.
    if user_id:
        owned = await SearchService().user_owned_platform_ids(user_id, platform_ids)
        owned_set = set(owned)
        rows = [r for r in rows if r.get("platform_id") in owned_set]
    # Merge per-user resource_id + AI status onto each hit so the card
    # click navigates to the correct /resources/file/<resource_id> URL
    # and the AI dot icons reflect real state. Search rows without a
    # matching resource (e.g., from a non-scoped ranker) keep the bare
    # parsed_media projection.
    if user_id:
        media_ids_for_hydration = [
            int(r["id"]) for r in rows if r.get("id") is not None
        ]
        resource_map = await _fetch_user_resources_by_media_id(
            user_id, media_ids_for_hydration
        )
        for row in rows:
            mid = row.get("id")
            if mid is None:
                continue
            extra = resource_map.get(int(mid))
            if extra:
                row.update(extra)
    by_pid = {r["platform_id"]: r for r in rows if r.get("platform_id")}
    # Preserve the ranking order from ``platform_ids`` — hits missing from the
    # DB (e.g., just deleted) are silently dropped.
    return [by_pid[pid] for pid in platform_ids if pid in by_pid]


@router.post("/semantic", response_model=SearchResponse)
async def semantic_search(
    request: SemanticSearchRequest,
    auth: AuthDep,
):
    """
    Search videos using natural language.

    Converts your query to an embedding and finds semantically similar videos.

    Examples:
    - "funny cat videos"
    - "cooking tutorial with pasta"
    - "travel vlog in Japan"
    """
    search_service = SearchService()

    try:
        response = await search_service.semantic_search(
            query=request.query,
            limit=request.limit,
            threshold=request.threshold,
            user_id=auth.user_id,
        )

        platform_ids = [r.platform_id for r in response.results]
        # semantic_search doesn't filter by user_id yet — scope hydration to the
        # rows the user owns so we don't leak cross-user parsed_media rows.
        videos = await _hydrate_media_by_platform_ids(
            platform_ids,
            user_id=auth.user_id,
        )
        # Filter ranked results to only include hits the user actually owns.
        owned_pids = {v["platform_id"] for v in videos if v.get("platform_id")}
        ranked_results = [r for r in response.results if r.platform_id in owned_pids]
        return SearchResponse(
            results=[
                SearchResultItem(
                    media_id=r.media_id,
                    platform_id=r.platform_id,
                    title=r.title,
                    description=r.description,
                    cover_url=r.cover_url,
                    similarity_score=round(r.similarity, 4),
                    tags=r.tags,
                    author=r.author,
                    view_count=r.view_count,
                    created_at=r.created_at,
                )
                for r in ranked_results
            ],
            videos=videos,
            total=len(ranked_results),
            query=response.query,
            search_type=response.search_type,
        )

    except EmbeddingSearchUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic search is temporarily unavailable.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed.",
        )


@router.post("/hybrid", response_model=SearchResponse)
async def hybrid_search(
    request: HybridSearchRequest,
    auth: AuthDep,
):
    """
    Combined search with semantic similarity and filters.

    You can:
    - Search by natural language query
    - Filter by tags, author, date range
    - Combine all of the above

    Results are ranked by semantic similarity when a query is provided.
    """
    search_service = SearchService()

    try:
        response = await search_service.hybrid_search(
            query=request.query or "",
            tag_ids=request.tag_ids,
            author=request.author,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
            threshold=request.threshold,
            user_id=auth.user_id,
            fields=request.fields,
        )

        platform_ids = [r.platform_id for r in response.results]
        # hybrid_search already filters by user_id internally; scope hydration
        # to the rows the user owns as a defensive guardrail too.
        videos = await _hydrate_media_by_platform_ids(
            platform_ids,
            user_id=auth.user_id,
        )
        return SearchResponse(
            results=[
                SearchResultItem(
                    media_id=r.media_id,
                    platform_id=r.platform_id,
                    title=r.title,
                    description=r.description,
                    cover_url=r.cover_url,
                    similarity_score=round(r.similarity, 4),
                    tags=r.tags,
                    author=r.author,
                    view_count=r.view_count,
                    created_at=r.created_at,
                )
                for r in response.results
            ],
            videos=videos,
            total=response.total,
            query=response.query,
            search_type=response.search_type,
        )

    except Exception as e:
        logger.error(f"Hybrid search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed.",
        )


# ---------------------------------------------------------------------------
# Plain-text ILIKE search — "give me everything that contains this keyword"
# ---------------------------------------------------------------------------
#
# Characters that would break a PostgREST ``or()`` filter value if passed
# through verbatim (comma separates filters, dot separates column.op, parens
# delimit nested filters, colon tails, plus quotes). We replace them with a
# space since none of them are meaningful inside typical search queries.
_PG_REST_FILTER_STRIP = str.maketrans({c: " " for c in ",.():\"'[]\\"})


@router.post("/text", response_model=SearchResponse)
async def text_search(
    request: TextSearchRequest,
    auth: AuthDep,
):
    """Plain-text ILIKE search across title / description / author / hashtags.

    Returns EVERY row whose searchable text contains the substring — no
    semantic ranking, no top-N cutoff (up to ``limit``, max 5000). Sorted
    by ``created_at DESC`` so newer matches come first.

    Use this when the user wants "give me all videos that contain the word
    'memory'" rather than "top 20 semantically similar videos".
    """
    q = request.query.strip()
    if not q:
        return SearchResponse(
            results=[],
            videos=[],
            total=0,
            query="",
            search_type="text",
        )
    # Sanitize special chars that would break PostgREST's or() filter
    # grammar. Keep letters / digits / CJK / spaces / common punctuation.
    q_safe = q.translate(_PG_REST_FILTER_STRIP).strip()
    if not q_safe:
        # User only typed special chars — nothing meaningful to match on.
        return SearchResponse(
            results=[],
            videos=[],
            total=0,
            query=q,
            search_type="text",
        )
    # Ready ILIKE pattern for the RPC (SQL ILIKE uses ``%`` wildcards).
    # ``%`` and ``_`` typed by the user are literals, not wildcards — without
    # escaping, a one-character "%" query matches every row in every scope.
    pattern = f"%{escape_like(q_safe)}%"

    # Empty fields list → no scope → empty result (caller probably means "no
    # scope checked", not "any scope").
    if not request.fields:
        return SearchResponse(
            results=[],
            videos=[],
            total=0,
            query=q,
            search_type="text",
        )

    # Scale Tier-1c: scope to rows THIS user owns via a JOIN RPC instead of
    # pre-fetching the user's full (1000-row-capped) media_id allowlist and
    # filtering parsed_media with a chunked ``.in_()``. ``parsed_media`` is a
    # global table — the RPC JOINs ``resources`` and filters
    # ``creator_id = user_id`` server-side, applies the multi-field ILIKE
    # (title / description / author / hashtags / transcript / notes / tags),
    # dedups per media, orders ``created_at DESC`` and limits — all in one
    # round-trip, with per-user ``resource_id`` + AI status already merged in.
    try:
        rows = await SearchService().search_user_media_text(
            user_id=auth.user_id,
            pattern=pattern,
            fields=list(request.fields),
            limit=request.limit,
        )
    except Exception as e:
        logger.error(f"Text search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed.",
        )

    # Project every row into a slim SearchResultItem (for analytics /
    # backwards-compat callers) AND include the full rows in ``videos``.
    results = [
        SearchResultItem(
            media_id=int(row["id"]) if row.get("id") is not None else 0,
            platform_id=row.get("platform_id") or "",
            title=row.get("title") or "",
            description=row.get("description"),
            cover_url=(
                (row.get("cover_urls") or [None])[0] if row.get("cover_urls") else None
            ),
            # Text search has no similarity score — fill a constant so the
            # UI sort doesn't surprise and analytics schemas stay valid.
            similarity_score=1.0,
            tags=row.get("tags") or [],
            author=row.get("author"),
            view_count=row.get("like_count") or 0,
            created_at=row.get("created_at"),
        )
        for row in rows
    ]
    return SearchResponse(
        results=results,
        videos=rows,
        total=len(rows),
        query=q,
        search_type="text",
    )


@router.get("/similar/{media_id}", response_model=SearchResponse)
async def find_similar_media(
    auth: AuthDep,
    media_id: int,
    limit: int = Query(10, ge=1, le=50, description="Maximum number of similar media"),
    threshold: float = Query(
        0.6, ge=0.0, le=1.0, description="Minimum similarity threshold"
    ),
):
    """
    Find media similar to a given media item.

    Uses the media's embedding to find semantically similar content.
    The source media is excluded from results.
    """
    search_service = SearchService()

    try:
        response = await search_service.find_similar_media(
            media_id=media_id,
            user_id=auth.user_id,
            limit=limit,
            threshold=threshold,
        )

        if response.total == 0:
            # Check if media exists and has embedding
            from app.repositories.analysis_repository import get_analysis_repository

            analysis_repo = get_analysis_repository()
            analysis = await analysis_repo.get_analysis(media_id)

            if not analysis:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Media not found or not analyzed yet. Run L1 analysis first.",
                )

            if not analysis.get("content_embedding"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Media has no embedding. Analysis may be incomplete.",
                )

        return SearchResponse(
            results=[
                SearchResultItem(
                    media_id=r.media_id,
                    platform_id=r.platform_id,
                    title=r.title,
                    description=r.description,
                    cover_url=r.cover_url,
                    similarity_score=round(r.similarity, 4),
                    tags=r.tags,
                    author=r.author,
                    view_count=r.view_count,
                    created_at=r.created_at,
                )
                for r in response.results
            ],
            total=response.total,
            query=response.query,
            search_type=response.search_type,
        )

    except HTTPException:
        raise
    except EmbeddingSearchUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic search is temporarily unavailable.",
        )
    except Exception as e:
        logger.error(f"Similar media search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed.",
        )


@router.get("/quick")
async def quick_search(
    auth: AuthDep,
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Quick search endpoint for search bar.

    Simplified semantic search with fewer options.
    """
    search_service = SearchService()

    try:
        response = await search_service.semantic_search(
            query=q,
            limit=limit,
            threshold=0.4,  # Lower threshold for broader results
            user_id=auth.user_id,
        )

        # Ownership guardrail, same as /semantic. The RPC is user-scoped since
        # migration 463, but this endpoint returns media_id / title / cover_url
        # straight to the browser, so it does not rely on a single layer.
        owned = await _hydrate_media_by_platform_ids(
            [r.platform_id for r in response.results],
            user_id=auth.user_id,
        )
        owned_pids = {v["platform_id"] for v in owned if v.get("platform_id")}
        results = [r for r in response.results if r.platform_id in owned_pids]

        # Return simplified format for quick display
        return {
            "results": [
                {
                    "media_id": r.media_id,
                    "title": r.title,
                    "cover_url": r.cover_url,
                    "similarity": round(r.similarity, 2),
                }
                for r in results
            ],
            "total": len(results),
        }

    except EmbeddingSearchUnavailable:
        # The engine cannot answer. Saying "0 results" here would be a wrong
        # answer dressed as a right one — the caller cannot tell it apart from
        # a genuine miss, and no probe would ever fire.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic search is temporarily unavailable.",
        )
    except HTTPException:
        raise
    except Exception as e:
        # Still not a silent empty page: log with context and surface a 500.
        logger.error(f"Quick search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed.",
        )
