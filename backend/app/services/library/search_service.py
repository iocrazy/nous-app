"""Semantic search service using vector embeddings (async optimized)."""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from app.db.session import read_scope
from app.repositories.analysis_repository import get_analysis_repository
from app.schemas.search import DEFAULT_SEARCH_FIELDS
from app.services.ai.providers.embedding_service import EmbeddingService
from app.services.library.like_escape import escape_like


@dataclass
class SearchResult:
    """A single search result."""

    media_id: int
    platform_id: str
    title: str
    description: Optional[str]
    cover_url: Optional[str]
    similarity: float
    tags: List[str] = field(default_factory=list)
    author: Optional[str] = None
    view_count: int = 0
    created_at: Optional[str] = None


@dataclass
class SearchResponse:
    """Response from search operation."""

    results: List[SearchResult]
    total: int
    query: str
    search_type: str  # "semantic", "hybrid", "similar"


class SearchService:
    """Service for semantic and hybrid video search (async optimized)."""

    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.analysis_repo = get_analysis_repository()

    async def search_user_media_text(
        self,
        user_id: str,
        pattern: Optional[str],
        fields: List[str],
        author: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Multi-field, user-scoped ILIKE search via the ``rpc_user_media_text_search``
        RPC (migration 274).

        ``parsed_media`` is a GLOBAL table — per-user ownership lives on
        ``resources``. The RPC JOINs the two and filters
        ``creator_id = user_id`` server-side, so there is no 1000-row-capped
        allowlist round-trip and no giant ``.in_()`` URL. Returns the card-view
        projection rows (``MediaRepository.CARD_SELECT`` columns + per-user
        ``resource_id`` / AI status), already ``DISTINCT`` per media and ordered
        ``created_at DESC``.

        ``pattern`` is a ready ILIKE pattern (``%foo%``); ``None`` means
        match-all (filter-only path). ``fields`` selects which scopes
        participate (title / description / author / hashtags / transcript /
        notes / tags / analysis).
        """
        # p_date_from / p_date_to are TEXT params in the RPC (migration 274),
        # so date strings pass through as-is — no timestamptz coercion. The
        # function returns a single jsonb object ({"rows": [...]}); asyncpg
        # may hand jsonb back as a str, so json.loads defensively.
        async with read_scope() as session:
            raw = (
                await session.execute(
                    text(
                        "SELECT public.rpc_user_media_text_search("
                        "CAST(:p_user_id AS uuid), :p_pattern, "
                        "CAST(:p_fields AS text[]), :p_author, "
                        ":p_date_from, :p_date_to, "
                        "CAST(:p_tag_ids AS text[]), :p_limit)"
                    ),
                    {
                        "p_user_id": str(user_id),
                        "p_pattern": pattern,
                        "p_fields": fields,
                        "p_author": author,
                        "p_date_from": date_from,
                        "p_date_to": date_to,
                        "p_tag_ids": [str(t) for t in tag_ids] if tag_ids else None,
                        "p_limit": limit,
                    },
                )
            ).scalar()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return payload.get("rows") or []

    async def user_owned_platform_ids(
        self, user_id: str, platform_ids: List[str]
    ) -> List[str]:
        """Return the subset of ``platform_ids`` the user owns via ``resources``.

        Calls ``rpc_user_owned_platform_ids`` (migration 274). The input is the
        small top-N ranker output, so the intersection is bounded — no
        1000-row cap, no URL blow-up. Replaces "fetch the full capped allowlist
        then intersect in Python".
        """
        if not platform_ids:
            return []
        # Returns a jsonb array; asyncpg may return it as a str → json.loads.
        async with read_scope() as session:
            raw = (
                await session.execute(
                    text(
                        "SELECT public.rpc_user_owned_platform_ids("
                        "CAST(:p_user_id AS uuid), CAST(:p_platform_ids AS text[]))"
                    ),
                    {
                        "p_user_id": str(user_id),
                        "p_platform_ids": [str(p) for p in platform_ids],
                    },
                )
            ).scalar()
        data = json.loads(raw) if isinstance(raw, str) else raw
        return list(data or [])

    async def semantic_search(
        self,
        query: str,
        limit: int = 20,
        threshold: float = 0.5,
        user_id: Optional[str] = None,
    ) -> SearchResponse:
        """
        Search videos using natural language query.

        Converts query to embedding and finds similar videos.
        """
        if not query or not query.strip():
            return SearchResponse(
                results=[], total=0, query=query, search_type="semantic"
            )

        # Generate embedding for query
        query_embedding = await self.embedding_service.generate_embedding(query)

        if not query_embedding:
            logger.warning("Failed to generate embedding for query")
            return SearchResponse(
                results=[], total=0, query=query, search_type="semantic"
            )

        # Search by embedding similarity
        if not user_id:
            # Every caller is authenticated; a missing id would mean scanning
            # every user's analysis rows, so refuse rather than widen.
            return SearchResponse(
                results=[], total=0, query=query, search_type="semantic"
            )

        raw_results = await self.analysis_repo.search_by_embedding(
            embedding=query_embedding,
            user_id=user_id,
            limit=limit,
            threshold=threshold,
        )

        # Transform results
        results = []
        for r in raw_results:
            results.append(
                SearchResult(
                    media_id=r["media_id"],
                    platform_id=r.get("platform_id", ""),
                    title=r.get("title", ""),
                    description=r.get("description"),
                    cover_url=(r.get("cover_urls") or [None])[0],
                    similarity=r.get("similarity", 0),
                    author=r.get("author"),
                    view_count=r.get("view_count", 0),
                    created_at=r.get("created_at"),
                )
            )

        return SearchResponse(
            results=results, total=len(results), query=query, search_type="semantic"
        )

    async def hybrid_search(
        self,
        query: str,
        tag_ids: Optional[List[str]] = None,
        author: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
        threshold: float = 0.4,
        user_id: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> SearchResponse:
        """
        Hybrid search combining semantic similarity with metadata filters.

        Scale Tier-1c: user-scoping is pushed into a JOIN RPC
        (``rpc_user_media_text_search``, migration 274) instead of pre-fetching
        the user's full media_id allowlist (PostgREST-capped at 1000 rows) and
        filtering ``parsed_media`` with a giant ``.in_()``. At 100k+ owned
        resources the old path silently dropped everything past the first 1000
        and risked a Kong URL-length 502.
        """
        # "My library" search is always user-scoped. Without a user_id there is
        # nothing to scope to — return empty rather than leak global rows (the
        # old no-user path returned cross-user results, which Tier-1c closes).
        if not user_id:
            return SearchResponse(
                results=[], total=0, query=query or "", search_type="hybrid"
            )

        # Normalize query for search (handle CJK text with spaces)
        query_clean = query.strip() if query else ""
        query_normalized = query_clean.replace(" ", "")  # "31 岁" -> "31岁"

        # Honour the caller's scope checkboxes. This used to be hardcoded to
        # the four basic fields, which silently discarded whatever the user had
        # ticked in the search-scope picker — Smart Search looked like it was
        # ignoring the UI because it was.
        #
        # An explicitly empty list means "no scope ticked" and returns nothing,
        # the same reading /search/text applies. Only an omitted argument falls
        # back to the default set; the two endpoints must not disagree about
        # what `fields: []` means.
        if fields is not None and len(fields) == 0:
            return SearchResponse(
                results=[], total=0, query=query or "", search_type="hybrid"
            )
        scope_fields = list(fields) if fields else list(DEFAULT_SEARCH_FIELDS)

        # If query is provided, do text search in database
        if query_clean:
            # Match anywhere in the text (ready ILIKE pattern for the RPC).
            # Metacharacters are escaped: a literal "%" typed by the user must
            # match a percent sign, not every row. Unescaped, a one-character
            # "%" query forces a full pass over every transcript body.
            search_pattern = f"%{escape_like(query_normalized)}%"

            # Primary path: title / description / author / hashtags, scoped to
            # this user via the JOIN RPC. author / date / tag filters are
            # applied inside the RPC (AND semantics).
            filtered_videos = await self.search_user_media_text(
                user_id=user_id,
                pattern=search_pattern,
                fields=scope_fields,
                author=author,
                date_from=date_from,
                date_to=date_to,
                tag_ids=tag_ids,
                limit=limit,
            )

            logger.info(
                f"Database text search found {len(filtered_videos)} results for: "
                f"{query_clean}"
            )

            # Fallback: if no results from the selected scope, search the visual
            # analysis text (resource_analysis.visual_description / detected_text)
            # — same user-scoped JOIN RPC, just a different field set.
            if not filtered_videos:
                logger.info(
                    f"No results in basic fields, searching resource_analysis for: "
                    f"{query_clean}"
                )
                filtered_videos = await self.search_user_media_text(
                    user_id=user_id,
                    pattern=search_pattern,
                    fields=["analysis"],
                    author=author,
                    date_from=date_from,
                    date_to=date_to,
                    tag_ids=tag_ids,
                    limit=limit,
                )
                if filtered_videos:
                    logger.info(
                        f"Found {len(filtered_videos)} results in resource_analysis"
                    )

            # Build results
            results = [
                SearchResult(
                    media_id=video["id"],
                    platform_id=video.get("platform_id", ""),
                    title=video.get("title", ""),
                    description=video.get("description"),
                    cover_url=(video.get("cover_urls") or [None])[0],
                    similarity=0.5,  # Default score for text matches
                    author=video.get("author"),
                    view_count=video.get("view_count", 0),
                    created_at=video.get("created_at"),
                )
                for video in filtered_videos
            ]

            return SearchResponse(
                results=results, total=len(results), query=query, search_type="hybrid"
            )

        # No query: filter-only path (match-all pattern + AND filters).
        filtered_videos = await self.search_user_media_text(
            user_id=user_id,
            pattern=None,
            fields=[],
            author=author,
            date_from=date_from,
            date_to=date_to,
            tag_ids=tag_ids,
            limit=limit,
        )

        results = [
            SearchResult(
                media_id=video["id"],
                platform_id=video.get("platform_id", ""),
                title=video.get("title", ""),
                description=video.get("description"),
                cover_url=(video.get("cover_urls") or [None])[0],
                similarity=1.0,  # No semantic ranking
                author=video.get("author"),
                view_count=video.get("view_count", 0),
                created_at=video.get("created_at"),
            )
            for video in filtered_videos
        ]

        return SearchResponse(
            results=results, total=len(results), query=query or "", search_type="hybrid"
        )

    async def find_similar_media(
        self,
        media_id: int,
        user_id: str,
        limit: int = 10,
        threshold: float = 0.6,
    ) -> SearchResponse:
        """
        Find media similar to a given media item.

        Uses the media's embedding to find semantically similar content.
        """
        # Get the source media's embedding
        analysis = await self.analysis_repo.get_analysis(media_id)

        if not analysis or not analysis.get("content_embedding"):
            logger.warning(f"No embedding found for media {media_id}")
            return SearchResponse(
                results=[],
                total=0,
                query=f"similar to media {media_id}",
                search_type="similar",
            )

        # Parse embedding from string format
        embedding = self._parse_embedding(analysis["content_embedding"])

        if not embedding:
            return SearchResponse(
                results=[],
                total=0,
                query=f"similar to media {media_id}",
                search_type="similar",
            )

        # Search for similar media (excluding the source)
        raw_results = await self.analysis_repo.search_by_embedding(
            embedding=embedding,
            user_id=user_id,
            limit=limit + 1,  # +1 to account for self-match
            threshold=threshold,
        )

        # Filter out the source media and transform results
        results = []
        for r in raw_results:
            if r["media_id"] != media_id:
                results.append(
                    SearchResult(
                        media_id=r["media_id"],
                        platform_id=r.get("platform_id", ""),
                        title=r.get("title", ""),
                        description=r.get("description"),
                        cover_url=(r.get("cover_urls") or [None])[0],
                        similarity=r.get("similarity", 0),
                        author=r.get("author"),
                        view_count=r.get("view_count", 0),
                        created_at=r.get("created_at"),
                    )
                )

        results = results[:limit]

        return SearchResponse(
            results=results,
            total=len(results),
            query=f"similar to media {media_id}",
            search_type="similar",
        )

    def _calculate_similarity(
        self, embedding1: List[float], embedding2_str: str
    ) -> float:
        """Calculate cosine similarity between embeddings."""
        try:
            embedding2 = self._parse_embedding(embedding2_str)
            if not embedding2:
                return 0.0

            # Cosine similarity
            dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
            norm1 = sum(a * a for a in embedding1) ** 0.5
            norm2 = sum(b * b for b in embedding2) ** 0.5

            if norm1 == 0 or norm2 == 0:
                return 0.0

            return dot_product / (norm1 * norm2)

        except Exception as e:
            logger.error(f"Error calculating similarity: {e}")
            return 0.0

    def _parse_embedding(self, embedding_str: str) -> Optional[List[float]]:
        """Parse embedding from PostgreSQL vector string format."""
        try:
            if isinstance(embedding_str, list):
                return embedding_str

            # Remove brackets and split
            clean_str = embedding_str.strip("[]")
            values = [float(x.strip()) for x in clean_str.split(",")]
            return values

        except Exception as e:
            logger.error(f"Error parsing embedding: {e}")
            return None
