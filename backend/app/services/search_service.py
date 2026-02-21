"""Semantic search service using vector embeddings (async optimized)."""

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.repositories.analysis_repository import AnalysisRepository
from app.services.embedding_service import EmbeddingService


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
        self.analysis_repo = AnalysisRepository()

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

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
        raw_results = await self.analysis_repo.search_by_embedding(
            embedding=query_embedding, limit=limit, threshold=threshold
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
                    cover_url=r.get("cover_url"),
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
    ) -> SearchResponse:
        """
        Hybrid search combining semantic similarity with metadata filters.
        Optimized: does text search at database level to reduce data transfer.
        """
        client = await self._get_client()

        # Normalize query for search (handle CJK text with spaces)
        query_clean = query.strip() if query else ""
        query_normalized = query_clean.replace(" ", "")  # "31 岁" -> "31岁"

        # If query is provided, do text search in database
        if query_clean:
            # Use database-level text search with ILIKE for better performance
            # Search pattern: match anywhere in the text
            search_pattern = f"%{query_normalized}%"

            # Build the search query with OR conditions using Supabase's or_ filter
            # We search in: title, description, author, hashtags
            base_query = client.table("parsed_media").select(
                "id, platform_id, title, description, cover_url, author, view_count, created_at, hashtags"
            )

            # Apply user filter via resources table (parsed_media is global)
            if user_id:
                user_resources = (
                    await client.table("resources")
                    .select("media_id")
                    .eq("creator_id", user_id)
                    .eq("is_trashed", False)
                    .execute()
                )
                user_media_ids = [r["media_id"] for r in user_resources.data if r.get("media_id")]
                if not user_media_ids:
                    return SearchResponse(results=[], total=0, query=query, search_type="hybrid")
                base_query = base_query.in_("id", user_media_ids)

            # Apply other filters
            if author:
                base_query = base_query.ilike("author", f"%{author}%")
            if date_from:
                base_query = base_query.gte("created_at", date_from)
            if date_to:
                base_query = base_query.lte("created_at", date_to)

            # Use or_ filter for text search across multiple columns
            # Supabase supports: or_(filter1,filter2,...)
            base_query = base_query.or_(
                f"title.ilike.{search_pattern},"
                f"description.ilike.{search_pattern},"
                f"author.ilike.{search_pattern},"
                f"hashtags.ilike.{search_pattern}"
            )

            # Execute the search query
            search_result = await base_query.limit(limit).execute()
            filtered_videos = search_result.data

            logger.info(
                f"Database text search found {len(filtered_videos)} results for: {query_clean}"
            )

            # If no results from basic fields, try searching in resource_analysis
            if not filtered_videos:
                logger.info(
                    f"No results in basic fields, searching resource_analysis for: {query_clean}"
                )

                # Get user's media IDs via resources table
                if user_id:
                    user_resources_2 = (
                        await client.table("resources")
                        .select("media_id")
                        .eq("creator_id", user_id)
                        .eq("is_trashed", False)
                        .execute()
                    )
                    user_media_ids = [r["media_id"] for r in user_resources_2.data if r.get("media_id")]
                else:
                    user_media = (
                        await client.table("parsed_media").select("id").limit(500).execute()
                    )
                    user_media_ids = [v["id"] for v in user_media.data]

                if user_media_ids:
                    # Search in resource_analysis table
                    analysis_search = (
                        await client.table("resource_analysis")
                        .select("resource_id")
                        .in_("resource_id", user_media_ids)
                        .or_(
                            f"visual_description.ilike.{search_pattern},"
                            f"detected_text.ilike.{search_pattern}"
                        )
                        .limit(limit)
                        .execute()
                    )

                    if analysis_search.data:
                        matched_media_ids = [
                            a["resource_id"] for a in analysis_search.data
                        ]
                        # Fetch the full media data for matched IDs
                        media_result = (
                            await client.table("parsed_media")
                            .select(
                                "id, platform_id, title, description, cover_url, author, view_count, created_at"
                            )
                            .in_("id", matched_media_ids)
                            .execute()
                        )
                        filtered_videos = media_result.data
                        logger.info(
                            f"Found {len(filtered_videos)} results in resource_analysis"
                        )

            # Apply tag filter if specified
            if tag_ids and filtered_videos:
                media_ids = [v["id"] for v in filtered_videos]
                tag_filter_result = (
                    await client.table("resource_tags")
                    .select("resource_id")
                    .in_("resource_id", media_ids)
                    .in_("tag_id", tag_ids)
                    .execute()
                )

                tagged_media_ids = set(r["resource_id"] for r in tag_filter_result.data)
                filtered_videos = [
                    v for v in filtered_videos if v["id"] in tagged_media_ids
                ]

            # Build results
            results = []
            for video in filtered_videos:
                results.append(
                    SearchResult(
                        media_id=video["id"],
                        platform_id=video.get("platform_id", ""),
                        title=video.get("title", ""),
                        description=video.get("description"),
                        cover_url=video.get("cover_url"),
                        similarity=0.5,  # Default score for text matches
                        author=video.get("author"),
                        view_count=video.get("view_count", 0),
                        created_at=video.get("created_at"),
                    )
                )

            return SearchResponse(
                results=results, total=len(results), query=query, search_type="hybrid"
            )

        # No query, just return filtered results
        base_query = client.table("parsed_media").select(
            "id, platform_id, title, description, cover_url, author, view_count, created_at"
        )

        if user_id:
            user_resources_3 = (
                await client.table("resources")
                .select("media_id")
                .eq("creator_id", user_id)
                .eq("is_trashed", False)
                .execute()
            )
            user_media_ids_3 = [r["media_id"] for r in user_resources_3.data if r.get("media_id")]
            if not user_media_ids_3:
                return SearchResponse(results=[], total=0, query=query or "", search_type="hybrid")
            base_query = base_query.in_("id", user_media_ids_3)
        if author:
            base_query = base_query.ilike("author", f"%{author}%")
        if date_from:
            base_query = base_query.gte("created_at", date_from)
        if date_to:
            base_query = base_query.lte("created_at", date_to)

        # Apply tag filter if specified
        if tag_ids:
            # Get resource IDs that have the specified tags
            tag_filter_result = (
                await client.table("resource_tags")
                .select("resource_id")
                .in_("tag_id", tag_ids)
                .execute()
            )
            tagged_media_ids = list(set(r["resource_id"] for r in tag_filter_result.data))

            if not tagged_media_ids:
                return SearchResponse(
                    results=[], total=0, query=query or "", search_type="hybrid"
                )

            base_query = base_query.in_("id", tagged_media_ids)

        filtered_result = await base_query.limit(limit).execute()
        filtered_videos = filtered_result.data

        results = []
        for video in filtered_videos:
            results.append(
                SearchResult(
                    media_id=video["id"],
                    platform_id=video.get("platform_id", ""),
                    title=video.get("title", ""),
                    description=video.get("description"),
                    cover_url=video.get("cover_url"),
                    similarity=1.0,  # No semantic ranking
                    author=video.get("author"),
                    view_count=video.get("view_count", 0),
                    created_at=video.get("created_at"),
                )
            )

        return SearchResponse(
            results=results, total=len(results), query=query or "", search_type="hybrid"
        )

    async def find_similar_media(
        self, media_id: int, limit: int = 10, threshold: float = 0.6
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
                        cover_url=r.get("cover_url"),
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
