"""API routes for Semantic Search."""

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.schemas.search import (
    HybridSearchRequest,
    SearchResponse,
    SearchResultItem,
    SemanticSearchRequest,
    TextSearchRequest,
)
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Search"])


async def _hydrate_media_by_platform_ids(
    platform_ids: List[str],
) -> List[Dict[str, Any]]:
    """Fetch full parsed_media rows for a list of platform_ids, preserving order.

    Search endpoints return slim ``SearchResultItem`` objects. The UI also
    needs the full ``ParsedMedia`` record (AI status, audio paths, like/
    comment/share/favorite counts, hashtags, etc.) to render the same card
    chrome on search hits as on regular library rows. This helper pulls those
    full rows in a single ``IN`` query and re-sorts to match the ranking.

    Returns empty list if ``platform_ids`` is empty.
    """
    if not platform_ids:
        return []
    client = await get_async_supabase_admin()
    result = (
        await client.table("parsed_media")
        .select("*")
        .in_("platform_id", platform_ids)
        .execute()
    )
    rows = result.data or []
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
        videos = await _hydrate_media_by_platform_ids(platform_ids)
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
        logger.error(f"Semantic search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
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
        )

        platform_ids = [r.platform_id for r in response.results]
        videos = await _hydrate_media_by_platform_ids(platform_ids)
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
            detail=f"Search failed: {str(e)}",
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
    pattern = f"*{q_safe}*"

    client = await get_async_supabase_admin()
    try:
        result = (
            await client.table("parsed_media")
            .select("*")
            .or_(
                f"title.ilike.{pattern},"
                f"description.ilike.{pattern},"
                f"author.ilike.{pattern},"
                f"hashtags.ilike.{pattern}"
            )
            .order("created_at", desc=True)
            .limit(request.limit)
            .execute()
        )
        rows: List[Dict[str, Any]] = result.data or []
    except Exception as e:
        logger.error(f"Text search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
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
            media_id=media_id, limit=limit, threshold=threshold
        )

        if response.total == 0:
            # Check if media exists and has embedding
            from app.repositories.analysis_repository import AnalysisRepository

            analysis_repo = AnalysisRepository()
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
    except Exception as e:
        logger.error(f"Similar media search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
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

        # Return simplified format for quick display
        return {
            "results": [
                {
                    "media_id": r.media_id,
                    "title": r.title,
                    "cover_url": r.cover_url,
                    "similarity": round(r.similarity, 2),
                }
                for r in response.results
            ],
            "total": response.total,
        }

    except Exception as e:
        logger.error(f"Quick search failed: {e}")
        return {"results": [], "total": 0}
