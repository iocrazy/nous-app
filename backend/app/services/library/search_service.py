"""Semantic search service using vector embeddings (async optimized)."""

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from app.db.session import read_scope
from app.repositories.analysis_repository import (
    EmbeddingSearchUnavailable,
    get_analysis_repository,
)
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
    # Hybrid only: what the vector leg did. One of VECTOR_LEG_OUTCOMES, None
    # for the other search types. A degraded hybrid answer is otherwise
    # byte-identical to a healthy one, and "never ran" must not read as
    # "matched nothing".
    vector_leg: Optional[str] = None


VECTOR_LEG_OUTCOMES = (
    "ok",
    "unconfigured",
    "embed_failed",
    "timeout",
    "unavailable",
    "error",
    "skipped_filters",
    "skipped_full_page",
    "skipped_no_scope",
    "skipped_no_query",
)

# An interactive search cannot wait the batch-workflow budget on the
# embedder; past this the hybrid answer goes out text-only.
HYBRID_EMBED_TIMEOUT_S = 5.0

# Asymmetric retrieval: the QUERY carries a task instruction, documents stay
# raw. Measured on the real library (2026-09-15, 57 keyword queries with
# ILIKE ground truth, recall@10): doubao-embedding-vision 0.48 -> 0.77,
# WeMM-2B 0.31 -> 0.66, WeMM-4B 0.19 -> 0.61, WeMM-9B 0.12 -> 0.60. Eight
# wordings were swept; this library-specific one is best or tied on every
# model (the generic "web search query" wording ties on doubao but loses
# 0.1 on 4B/9B). Provider-agnostic (a text prefix, not a vendor field), so
# it survives a model switch; documents embedded by analyze_l1 / the
# backfill are unaffected and need no re-embed.
QUERY_INSTRUCTION = (
    "Instruct: Given a search keyword, retrieve short-video titles and "
    "descriptions that contain or are about this keyword\nQuery: "
)


def query_text(query: str) -> str:
    """Text to embed for a SEARCH query (never for a document)."""
    return QUERY_INSTRUCTION + query.strip()


def _row_to_result(r: Dict[str, Any]) -> SearchResult:
    """One mapping for every ``search_by_embedding`` row consumer."""
    return SearchResult(
        media_id=int(r["media_id"]),
        platform_id=r.get("platform_id", ""),
        title=r.get("title", ""),
        description=r.get("description"),
        cover_url=(r.get("cover_urls") or [None])[0],
        similarity=float(r.get("similarity", 0) or 0),
        author=r.get("author"),
        view_count=r.get("view_count", 0),
        created_at=r.get("created_at"),
    )


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
        query_embedding = await self.embedding_service.generate_embedding(
            query_text(query)
        )

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

        results = [
            _row_to_result(r) for r in raw_results if r.get("media_id") is not None
        ]

        return SearchResponse(
            results=results, total=len(results), query=query, search_type="semantic"
        )

    async def _vector_hits(
        self, query: str, user_id: str, limit: int, threshold: float
    ) -> tuple[List[SearchResult], str]:
        """Cosine neighbours of ``query`` for the hybrid merge. Best-effort.

        Returns ``(hits, outcome)`` where outcome is one of
        ``VECTOR_LEG_OUTCOMES``. Empty when the embedder is unconfigured, the
        query cannot be embedded, the embedder is slower than an interactive
        search can wait, or the engine RPC is missing (deployment window
        before migration 463). Those are logged, not raised: hybrid already
        holds a valid text answer, and the vector part is additive. The
        outcome travels with the empty list so the response can SAY the leg
        degraded instead of dressing "never ran" as "matched nothing".
        """
        try:
            vec, reason = await asyncio.wait_for(
                self.embedding_service.try_embed(query_text(query)),
                timeout=HYBRID_EMBED_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[hybrid] query embedding exceeded {HYBRID_EMBED_TIMEOUT_S}s, "
                "text-only"
            )
            return [], "timeout"
        except Exception as e:  # noqa: BLE001 — belt to try_embed's brace
            logger.error(f"[hybrid] query embedding raised, text-only: {e}")
            return [], "embed_failed"
        if vec is None:
            if reason == "unconfigured":
                return [], "unconfigured"
            logger.error(f"[hybrid] query embedding skipped: {reason}")
            return [], "embed_failed"
        try:
            rows = await self.analysis_repo.search_by_embedding(
                embedding=vec, user_id=user_id, limit=limit, threshold=threshold
            )
        except EmbeddingSearchUnavailable as e:
            logger.error(f"[hybrid] vector engine unavailable, text-only: {e}")
            return [], "unavailable"
        except Exception as e:  # noqa: BLE001 — the text half is still the answer
            logger.error(f"[hybrid] vector leg failed, text-only: {e}")
            return [], "error"
        hits = [_row_to_result(r) for r in rows if r.get("media_id") is not None]
        return hits, "ok"

    @staticmethod
    def _merge_text_and_vector(
        text_hits: List[SearchResult], vector_hits: List[SearchResult], limit: int
    ) -> List[SearchResult]:
        """Text hits first (exact substring, similarity pinned to 1.0), then
        vector-only hits by cosine. Deduped on media_id, text wins. Inputs
        are not mutated: pinned text hits are fresh copies."""
        seen: set[int] = set()
        merged: List[SearchResult] = []
        for h in text_hits:
            if h.media_id in seen:
                continue
            seen.add(h.media_id)
            merged.append(replace(h, similarity=1.0))
        extra = sorted(vector_hits, key=lambda h: h.similarity, reverse=True)
        for h in extra:
            # The engine returns one row per analysis row, so a media can
            # arrive twice; keep its best score only.
            if h.media_id in seen:
                continue
            seen.add(h.media_id)
            merged.append(h)
        return merged[:limit]

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
        Hybrid search: ILIKE text hits merged with cosine vector hits.

        Text hits (exact substring over the caller's scope) rank first with
        similarity 1.0; vector-only hits follow by cosine score. The vector
        leg is best-effort (see ``_vector_hits``) — unconfigured or unavailable
        degrades to text-only, never to a 500. Before this the endpoint ran
        the same ILIKE as ``/search/text`` and nothing else, despite the
        "AI + keywords" label in the picker.

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
                results=[],
                total=0,
                query=query or "",
                search_type="hybrid",
                vector_leg="skipped_no_scope",
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
                results=[],
                total=0,
                query=query or "",
                search_type="hybrid",
                vector_leg="skipped_no_scope",
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

            text_hits = [
                SearchResult(
                    media_id=video["id"],
                    platform_id=video.get("platform_id", ""),
                    title=video.get("title", ""),
                    description=video.get("description"),
                    cover_url=(video.get("cover_urls") or [None])[0],
                    similarity=1.0,  # exact substring; pinned in the merge too
                    author=video.get("author"),
                    view_count=video.get("view_count", 0),
                    created_at=video.get("created_at"),
                )
                for video in filtered_videos
            ]

            # The vector RPC knows nothing about author / date / tag filters,
            # so running it under a filtered query would append rows the
            # caller explicitly excluded. Text-only in that case, and say so.
            vector_hits: List[SearchResult] = []
            if tag_ids or author or date_from or date_to:
                vector_leg = "skipped_filters"
            elif len(text_hits) >= limit:
                # Vector hits only ever rank BELOW text hits, so when the text
                # leg already fills the page the embedding call buys nothing.
                vector_leg = "skipped_full_page"
            else:
                # Ask for a full page: neighbours that overlap the text hits
                # are dropped in the merge, so a right-sized ask underfills.
                vector_hits, vector_leg = await self._vector_hits(
                    query_clean, user_id, limit=limit, threshold=threshold
                )
            if vector_hits:
                logger.info(
                    f"[hybrid] {len(vector_hits)} vector hits merged under "
                    f"{len(text_hits)} text hits for: {query_clean}"
                )
            results = self._merge_text_and_vector(text_hits, vector_hits, limit)

            return SearchResponse(
                results=results,
                total=len(results),
                query=query,
                search_type="hybrid",
                vector_leg=vector_leg,
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
            results=results,
            total=len(results),
            query=query or "",
            search_type="hybrid",
            vector_leg="skipped_no_query",
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
