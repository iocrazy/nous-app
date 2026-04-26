"""API routes for Semantic Search."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.media_repository import MediaRepository
from app.schemas.search import (
    HybridSearchRequest,
    SearchResponse,
    SearchResultItem,
    SemanticSearchRequest,
    TextSearchRequest,
)
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Search"])


async def _fetch_user_media_ids(user_id: str) -> List[int]:
    """Return the ``parsed_media.id`` list this user owns via ``resources``.

    ``parsed_media`` is a **global** table — one row per platform_id across
    every user in the system. Per-user ownership lives on ``resources``
    (``creator_id`` + ``media_id`` FK to ``parsed_media.id`` + ``is_trashed``
    + ``source_type='web'``). Every search that should only return "my
    library" must first look up this list and scope the parsed_media query
    to it, otherwise results leak across users.
    """
    client = await get_async_supabase_admin()
    result = (
        await client.table("resources")
        .select("media_id")
        .eq("creator_id", user_id)
        .eq("source_type", "web")
        .eq("is_trashed", False)
        .execute()
    )
    return [int(r["media_id"]) for r in (result.data or []) if r.get("media_id")]


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

    Empty ``media_ids`` short-circuits to avoid an unnecessary query.
    """
    if not media_ids:
        return {}
    client = await get_async_supabase_admin()
    result = (
        await client.table("resources")
        .select(
            "id, media_id, transcript_status, summary_status, visual_analysis_status"
        )
        .eq("creator_id", user_id)
        .eq("source_type", "web")
        .eq("is_trashed", False)
        .in_("media_id", media_ids)
        .execute()
    )
    out: Dict[int, Dict[str, Any]] = {}
    for row in result.data or []:
        mid = row.get("media_id")
        if mid is None:
            continue
        out[int(mid)] = {
            "resource_id": str(row["id"]) if row.get("id") is not None else None,
            "transcript_status": row.get("transcript_status"),
            "summary_status": row.get("summary_status"),
            "visual_analysis_status": row.get("visual_analysis_status"),
        }
    return out


async def _hydrate_media_by_platform_ids(
    platform_ids: List[str],
    user_media_ids: Optional[List[int]] = None,
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

    When ``user_media_ids`` is provided, the hydration is further scoped to
    rows that exist in the user's ``resources`` — prevents cross-user leak
    for callers whose platform_ids came from a non-scoped ranker (e.g.
    ``semantic_search`` doesn't currently filter by user).

    Returns empty list if ``platform_ids`` is empty.
    """
    if not platform_ids:
        return []
    client = await get_async_supabase_admin()
    # If ``user_media_ids`` would push the URL past nginx's 8KB limit
    # (each Snowflake id is ~15 chars, plus separators), drop the
    # ``id IN`` filter and intersect in Python instead. ``platform_id IN``
    # is already small (top-N ranker output), so this is safe.
    URL_SAFE_LIMIT = 100
    if user_media_ids is not None:
        if not user_media_ids:
            return []
        if len(user_media_ids) <= URL_SAFE_LIMIT:
            query = (
                client.table("parsed_media")
                .select(MediaRepository.CARD_SELECT)
                .in_("platform_id", platform_ids)
                .in_("id", user_media_ids)
            )
            result = await query.execute()
            rows = result.data or []
        else:
            # Too many ids for a single URL — fetch by platform_id then
            # intersect with the allowlist in memory. Cheap because the
            # platform_ids ranker has already trimmed to a few dozen.
            user_id_set = set(user_media_ids)
            query = (
                client.table("parsed_media")
                .select(MediaRepository.CARD_SELECT)
                .in_("platform_id", platform_ids)
            )
            result = await query.execute()
            rows = [r for r in (result.data or []) if r.get("id") in user_id_set]
    else:
        query = (
            client.table("parsed_media")
            .select(MediaRepository.CARD_SELECT)
            .in_("platform_id", platform_ids)
        )
        result = await query.execute()
        rows = result.data or []
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
        # semantic_search doesn't filter by user_id yet — scope hydration via
        # user_media_ids so we don't leak cross-user parsed_media rows.
        user_media_ids = await _fetch_user_media_ids(auth.user_id)
        videos = await _hydrate_media_by_platform_ids(
            platform_ids,
            user_media_ids=user_media_ids,
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
        # hybrid_search already filters by user_id internally, but we still
        # pass user_media_ids to hydration as a defensive guardrail in case
        # of a race between the ranker query and the hydration.
        user_media_ids = await _fetch_user_media_ids(auth.user_id)
        videos = await _hydrate_media_by_platform_ids(
            platform_ids,
            user_media_ids=user_media_ids,
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

    # CRITICAL: scope to rows THIS user owns via ``resources``. parsed_media
    # is a global table — searching it directly returns every user's library
    # mashed together. Get the per-user media_id allowlist first.
    user_media_ids = await _fetch_user_media_ids(auth.user_id)
    if not user_media_ids:
        return SearchResponse(
            results=[],
            videos=[],
            total=0,
            query=q,
            search_type="text",
        )

    # Build OR filter dynamically from selected fields. Empty fields list →
    # no scope → empty result (caller probably means "no scope checked",
    # not "any scope").
    if not request.fields:
        return SearchResponse(
            results=[],
            videos=[],
            total=0,
            query=q,
            search_type="text",
        )

    client = await get_async_supabase_admin()

    # Direct parsed_media columns map straight to ILIKE filters.
    column_map = {
        "title": "title",
        "description": "description",
        "author": "author",
        "hashtags": "hashtags",
        "transcript": "ai_extract_text",
    }
    direct_or_parts = [
        f"{column_map[f]}.ilike.{pattern}" for f in request.fields if f in column_map
    ]

    # tags / notes need pre-queries — they live on other tables and
    # contribute media_ids that we OR into the main filter via id.in.(...)
    extra_media_ids: set[int] = set()
    try:
        if "tags" in request.fields:
            tags_q = (
                await client.table("tags").select("id").ilike("name", pattern).execute()
            )
            tag_ids = [t["id"] for t in (tags_q.data or []) if t.get("id") is not None]
            if tag_ids:
                rt_q = (
                    await client.table("resource_tags")
                    .select("resource_id")
                    .in_("tag_id", tag_ids)
                    .execute()
                )
                resource_ids = [
                    r["resource_id"] for r in (rt_q.data or []) if r.get("resource_id")
                ]
                if resource_ids:
                    res_q = (
                        await client.table("resources")
                        .select("media_id")
                        .in_("id", resource_ids)
                        .eq("creator_id", auth.user_id)
                        .eq("source_type", "web")
                        .eq("is_trashed", False)
                        .execute()
                    )
                    for r in res_q.data or []:
                        if r.get("media_id"):
                            extra_media_ids.add(int(r["media_id"]))

        if "notes" in request.fields:
            notes_q = (
                await client.table("resources")
                .select("media_id")
                .eq("creator_id", auth.user_id)
                .eq("source_type", "web")
                .eq("is_trashed", False)
                .ilike("notes", pattern)
                .execute()
            )
            for r in notes_q.data or []:
                if r.get("media_id"):
                    extra_media_ids.add(int(r["media_id"]))
    except Exception as e:
        logger.error(f"Text search side-query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )

    or_parts = list(direct_or_parts)
    if extra_media_ids:
        # Intersect with user_media_ids (defense in depth — already-scoped
        # but cheap to re-confirm).
        scoped = extra_media_ids & set(user_media_ids)
        if scoped:
            ids_csv = ",".join(str(i) for i in scoped)
            or_parts.append(f"id.in.({ids_csv})")

    # If the only selected scopes were tags/notes and they yielded zero
    # extra_media_ids AND there are no direct fields, no rows can match.
    if not or_parts:
        return SearchResponse(
            results=[],
            videos=[],
            total=0,
            query=q,
            search_type="text",
        )

    or_filter = ",".join(or_parts)
    # Chunk the user_media_ids — PostgREST encodes ``in_(...)`` into a
    # query-string filter, and a few hundred 18-digit Snowflake ids blow
    # past nginx's URI length limit (8KB) → 414. Each chunk is sized so
    # ``id=in.(id1,id2,…)`` plus the OR filter and base URL stays under
    # ~6KB. Results are merged + re-sorted client-side, then trimmed to
    # ``request.limit``.
    URL_SAFE_CHUNK = 100
    rows: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()
    try:
        for start in range(0, len(user_media_ids), URL_SAFE_CHUNK):
            chunk = user_media_ids[start : start + URL_SAFE_CHUNK]
            chunk_result = (
                await client.table("parsed_media")
                .select(MediaRepository.CARD_SELECT)
                .in_("id", chunk)
                .or_(or_filter)
                .order("created_at", desc=True)
                .limit(request.limit)
                .execute()
            )
            for row in chunk_result.data or []:
                rid = row.get("id")
                if rid is None or rid in seen_ids:
                    continue
                seen_ids.add(rid)
                rows.append(row)
    except Exception as e:
        logger.error(f"Text search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )

    # Merge + global sort + trim. Each chunk was sorted DESC and limited,
    # but cross-chunk order needs a final re-sort to match the contract.
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    rows = rows[: request.limit]

    # Merge per-user resource_id + AI status onto each row so the card
    # click navigates to the right /resources/file/<resource_id> URL and
    # the AI dot icons reflect real state. See _hydrate_media_by_platform_ids
    # for the same pattern on the semantic / hybrid endpoints.
    media_ids_for_hydration = [int(r["id"]) for r in rows if r.get("id") is not None]
    resource_map = await _fetch_user_resources_by_media_id(
        auth.user_id, media_ids_for_hydration
    )
    for row in rows:
        mid = row.get("id")
        if mid is None:
            continue
        extra = resource_map.get(int(mid))
        if extra:
            row.update(extra)

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
