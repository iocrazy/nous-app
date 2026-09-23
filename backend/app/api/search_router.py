"""API routes for Semantic Search."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.core.embedding_space import SEMANTIC_LAYER, EmbeddingDimensionMismatch
from app.repositories.analysis_repository import EmbeddingSearchUnavailable
from app.repositories.embedding_space_repository import (
    get_embedding_space_repository,
)
from app.repositories.resource_embeddings_repository import (
    EmbeddingStoreMissing,
    get_resource_embeddings_repository,
)
from app.schemas.search import (
    HybridSearchRequest,
    LayerStatus,
    SearchResponse,
    SearchResultItem,
    SemanticSearchRequest,
    SpaceInfo,
    TextSearchRequest,
    VectorsStatusResponse,
)
from app.schemas.unified_search import UnifiedSearchResponse
from app.services.ai.providers.embedding_service import EmbeddingService
from app.services.library.like_escape import escape_like
from app.services.library.search_service import (
    SearchFiltersUnavailable,
    SearchService,
)
from app.services.search.service import ALL_SEARCH_KINDS, unified_search

router = APIRouter(prefix="/search", tags=["Search"])

# The embedder is configured with a model whose vectors do not fit the columns
# (app.core.embedding_space). Typed so a client / probe can tell "semantic
# search is misconfigured" from "the engine is briefly unavailable".
_DIMENSION_MISMATCH_DETAIL = {
    "code": "embedding_dimension_mismatch",
    "message": "Semantic search is unavailable: the embedding model does not "
    "match the vector store.",
}


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
                    layer=r.layer,
                )
                for r in ranked_results
            ],
            videos=videos,
            total=len(ranked_results),
            query=response.query,
            search_type=response.search_type,
        )

    except EmbeddingDimensionMismatch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DIMENSION_MISMATCH_DETAIL,
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
            filters=request.filters,
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
                    layer=r.layer,
                )
                for r in response.results
            ],
            videos=videos,
            total=response.total,
            query=response.query,
            search_type=response.search_type,
            vector_leg=response.vector_leg,
            legs=response.legs,
            reranked=response.reranked,
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
            filters=request.filters,
        )
    except SearchFiltersUnavailable as e:
        # Typed, and 503 rather than 500: retryable, and it says the search is
        # unavailable instead of returning hits that ignore the user's filters.
        logger.error(f"Text search unavailable (migration 475 not applied): {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search is briefly unavailable while an update finishes.",
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


# ``embedding_spaces.id`` is a positive Snowflake, never 0: counting coverage
# against 0 yields "covered 0 of the caller's total" without any space.
NO_SPACE_ID = 0


def _layers(covered: int, total: int) -> List[LayerStatus]:
    return [
        LayerStatus(
            layer="semantic",
            status="ok" if covered else "not_built",
            covered=covered,
            total=total,
        ),
        # Enum slot only until the transcript layer ships.
        LayerStatus(layer="transcript", status="not_built", covered=0, total=total),
    ]


@router.get("/vectors/status", response_model=VectorsStatusResponse)
async def vectors_status(auth: AuthDep):
    """How much of the caller's library has a vector, per retrieval layer, in
    the CURRENT embedding space (the admin-configured embedder).

    ``status`` is "ok", "unconfigured" (no embedder: ``space`` null, coverage
    0 of the caller's total) or "store_missing" (migration 494 not applied:
    ``space`` null, ``layers`` empty). A typed answer in every case, never a
    500 — the UI shows it next to the search box.
    """
    embedder = EmbeddingService()
    spec = await embedder.space_spec()
    repo = get_resource_embeddings_repository()
    if spec is None:
        try:
            _, total = await repo.coverage(
                user_id=auth.user_id, space_id=NO_SPACE_ID, layer=SEMANTIC_LAYER
            )
        except EmbeddingStoreMissing:
            return VectorsStatusResponse(space=None, status="unconfigured", layers=[])
        return VectorsStatusResponse(
            space=None, status="unconfigured", layers=_layers(0, total)
        )
    try:
        space = await get_embedding_space_repository().get_or_create(spec)
        covered, total = await repo.coverage(
            user_id=auth.user_id, space_id=space["id"], layer=SEMANTIC_LAYER
        )
    except EmbeddingStoreMissing as e:
        logger.error(f"Vector status: store missing (migration 494): {e}")
        return VectorsStatusResponse(space=None, status="store_missing", layers=[])
    return VectorsStatusResponse(
        space=SpaceInfo(**{k: space[k] for k in SpaceInfo.model_fields}),
        status="ok",
        layers=_layers(covered, total),
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

        if response.source_embedded is False:
            # Nothing to compare with — not "no neighbours". The service
            # looked in resource_embeddings (current space) and the legacy
            # column; a resource without a vector gets one from the backfill.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Media has no embedding yet. Run the embedding backfill first.",
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
                    layer=r.layer,
                )
                for r in response.results
            ],
            total=response.total,
            query=response.query,
            search_type=response.search_type,
        )

    except HTTPException:
        raise
    except EmbeddingDimensionMismatch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DIMENSION_MISMATCH_DETAIL,
        )
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

    except EmbeddingDimensionMismatch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DIMENSION_MISMATCH_DETAIL,
        )
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


# ---------------------------------------------------------------------------
# Unified search across issues / runs / outputs (harness 三期 3c §2.3)
#
# 挂在**同一个** router 上而不是新开一个：``/search`` 这个前缀已经是它的，
# 而本端点占的是裸路径（``GET /api/v1/search``），与上面五条子路径
# （``/semantic`` ``/hybrid`` ``/text`` ``/similar/{id}`` ``/quick``）不相交。
# 另起一个同前缀的 router 只会让「谁拥有 /search」变成两个答案。
#
# 上面那些是**资源库**检索（parsed_media / 语义向量）；这一条是**工作**检索
# （议题 / run / 产出）。两者共享前缀但不共享任何数据源、响应形状或可见性
# 规则——所以响应模型也分在两个模块里，见 ``schemas/unified_search.py``。
#
# **没有模块门。** ``/outputs`` 骑 ``todolist`` 的门是因为它只服务议题详情页的
# 血缘面板；检索是跨模块的入口（⌘K），对着没开某个模块的人关掉整个搜索框，
# 等于让他搜不到自己本来就能看见的东西。可见性由 ``unified_search`` 的两道门
# 负责，那才是这个端点的边界。
#
# **拒绝只有一种形状：typed 400。** 越权不在这里拒绝——它在服务层化成
# 空组 + 200（跨团队端点上，「不许你看」和「不存在」必须同一个答案）。
# ---------------------------------------------------------------------------

#: 一个字的查询在 trgm 上退化成全表匹配，而它几乎一定是「还在打字」。
MIN_QUERY_CHARS = 2


def _reject_search(code: str, message: str) -> HTTPException:
    # detail 必须是 dict：生产的 ErrorResponse 外壳只让 dict 落到 details，
    # 字符串会塌成 http_400 + "400 Bad Request"（CLAUDE.md 2026-09-09）。
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )


@router.get("", response_model=UnifiedSearchResponse)
async def unified_search_endpoint(
    auth: AuthDep,
    q: str = Query(..., max_length=200),
    kinds: Optional[str] = Query(None),
    team_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    issue_id: Optional[int] = Query(None),
    limit_per_group: int = Query(10, ge=1, le=50),
) -> UnifiedSearchResponse:
    """三组命中 + 每组总数 + 服务端墙钟。

    ``limit_per_group`` 的上限在签名里：分组裁剪在 Python 层做，SQL 要按它的
    三倍取，所以一个没上限的值会让数据库替调用方的笔误买单。
    """
    term = q.strip()
    if len(term) < MIN_QUERY_CHARS:
        raise _reject_search(
            "query_too_short", f"a search needs at least {MIN_QUERY_CHARS} characters"
        )
    asked = {k.strip() for k in (kinds or "").split(",") if k.strip()}
    # ``kinds`` 缺席、空串、或只有逗号 —— 三者都是「没挑」，给全部三组。
    # 只有「挑了，但挑的全不认得」才是拒绝。
    if not asked:
        wanted = set(ALL_SEARCH_KINDS)
    else:
        # 认得的留下，认不得的丢掉——新版本前端多送一个 kind 不该让整次搜索
        # 失败。全都认不得才是拒绝：那时调用方要的东西一件没给。
        wanted = asked & ALL_SEARCH_KINDS
        if not wanted:
            raise _reject_search(
                "unknown_kinds", f"none of {sorted(asked)} is a searchable kind"
            )
    return await unified_search(
        auth=auth,
        q=term,
        kinds=wanted,
        team_id=team_id,
        project_id=project_id,
        issue_id=issue_id,
        limit_per_group=limit_per_group,
    )
