"""Semantic search service using vector embeddings (异步)."""
from typing import Optional, List
from dataclasses import dataclass, field

from loguru import logger

from app.services.embedding_service import EmbeddingService
from app.repositories.analysis_repository import AnalysisRepository
from app.db.supabase_client import get_async_supabase_admin


@dataclass
class SearchResult:
    """A single search result."""
    video_id: int
    title: str
    description: Optional[str]
    cover_url: Optional[str]
    similarity: float
    tags: List[str] = field(default_factory=list)
    author: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class SearchResponse:
    """Response from search operation."""
    results: List[SearchResult]
    total: int
    query: str
    search_type: str  # "semantic", "hybrid", "similar"


class SearchService:
    """Service for semantic and hybrid video search (异步)."""

    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.analysis_repo = AnalysisRepository()
        self._client = None

    async def _get_client(self):
        """获取异步客户端"""
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    async def semantic_search(
        self,
        query: str,
        limit: int = 20,
        threshold: float = 0.5,
        user_id: Optional[str] = None
    ) -> SearchResponse:
        """
        Search videos using natural language query.

        Converts query to embedding and finds similar videos.
        """
        if not query or not query.strip():
            return SearchResponse(
                results=[],
                total=0,
                query=query,
                search_type="semantic"
            )

        # Generate embedding for query
        query_embedding = await self.embedding_service.generate_embedding(query)

        if not query_embedding:
            logger.warning("Failed to generate embedding for query")
            return SearchResponse(
                results=[],
                total=0,
                query=query,
                search_type="semantic"
            )

        # Search by embedding similarity
        raw_results = await self.analysis_repo.search_by_embedding(
            embedding=query_embedding,
            limit=limit,
            threshold=threshold
        )

        # Transform results
        results = []
        for r in raw_results:
            results.append(SearchResult(
                video_id=r["video_id"],
                title=r.get("title", ""),
                description=r.get("description"),
                cover_url=r.get("cover_url"),
                similarity=r.get("similarity", 0)
            ))

        return SearchResponse(
            results=results,
            total=len(results),
            query=query,
            search_type="semantic"
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
        user_id: Optional[str] = None
    ) -> SearchResponse:
        """
        Hybrid search combining semantic similarity with metadata filters.

        Applies filters first, then ranks by semantic similarity.
        """
        client = await self._get_client()

        # Start with base query
        base_query = client.table("douyin_videos").select(
            "id, title, desc, cover_url, author, created_at"
        )

        # Apply user filter if provided
        if user_id:
            base_query = base_query.eq("user_id", user_id)

        # Apply author filter
        if author:
            base_query = base_query.ilike("author", f"%{author}%")

        # Apply date filters
        if date_from:
            base_query = base_query.gte("created_at", date_from)
        if date_to:
            base_query = base_query.lte("created_at", date_to)

        # Get filtered videos
        filtered_result = await base_query.limit(500).execute()
        filtered_videos = filtered_result.data

        if not filtered_videos:
            return SearchResponse(
                results=[],
                total=0,
                query=query,
                search_type="hybrid"
            )

        # Apply tag filter if specified
        if tag_ids:
            video_ids = [v["id"] for v in filtered_videos]
            tag_filter_result = await client.table("video_tags").select(
                "video_id"
            ).in_("video_id", video_ids).in_("tag_id", tag_ids).execute()

            tagged_video_ids = set(r["video_id"] for r in tag_filter_result.data)
            filtered_videos = [v for v in filtered_videos if v["id"] in tagged_video_ids]

        if not filtered_videos:
            return SearchResponse(
                results=[],
                total=0,
                query=query,
                search_type="hybrid"
            )

        # If query is provided, rank by semantic similarity
        if query and query.strip():
            query_embedding = await self.embedding_service.generate_embedding(query)

            if query_embedding:
                # Get embeddings for filtered videos
                video_ids = [v["id"] for v in filtered_videos]
                analysis_result = await client.table("video_analysis").select(
                    "video_id, content_embedding"
                ).in_("video_id", video_ids).not_.is_("content_embedding", "null").execute()

                # Calculate similarities
                video_similarities = {}
                for analysis in analysis_result.data:
                    if analysis.get("content_embedding"):
                        similarity = self._calculate_similarity(
                            query_embedding,
                            analysis["content_embedding"]
                        )
                        if similarity >= threshold:
                            video_similarities[analysis["video_id"]] = similarity

                # Sort filtered videos by similarity
                results = []
                for video in filtered_videos:
                    video_id = video["id"]
                    similarity = video_similarities.get(video_id, 0)

                    if similarity > 0 or not query.strip():
                        results.append(SearchResult(
                            video_id=video_id,
                            title=video.get("title", ""),
                            description=video.get("desc"),
                            cover_url=video.get("cover_url"),
                            similarity=similarity,
                            author=video.get("author"),
                            created_at=video.get("created_at")
                        ))

                # Sort by similarity descending
                results.sort(key=lambda x: x.similarity, reverse=True)
                results = results[:limit]

                return SearchResponse(
                    results=results,
                    total=len(results),
                    query=query,
                    search_type="hybrid"
                )

        # No query, just return filtered results
        results = []
        for video in filtered_videos[:limit]:
            results.append(SearchResult(
                video_id=video["id"],
                title=video.get("title", ""),
                description=video.get("desc"),
                cover_url=video.get("cover_url"),
                similarity=1.0,  # No semantic ranking
                author=video.get("author"),
                created_at=video.get("created_at")
            ))

        return SearchResponse(
            results=results,
            total=len(results),
            query=query or "",
            search_type="hybrid"
        )

    async def find_similar_videos(
        self,
        video_id: int,
        limit: int = 10,
        threshold: float = 0.6
    ) -> SearchResponse:
        """
        Find videos similar to a given video.

        Uses the video's embedding to find semantically similar content.
        """
        # Get the source video's embedding
        analysis = await self.analysis_repo.get_analysis(video_id)

        if not analysis or not analysis.get("content_embedding"):
            logger.warning(f"No embedding found for video {video_id}")
            return SearchResponse(
                results=[],
                total=0,
                query=f"similar to video {video_id}",
                search_type="similar"
            )

        # Parse embedding from string format
        embedding = self._parse_embedding(analysis["content_embedding"])

        if not embedding:
            return SearchResponse(
                results=[],
                total=0,
                query=f"similar to video {video_id}",
                search_type="similar"
            )

        # Search for similar videos (excluding the source video)
        raw_results = await self.analysis_repo.search_by_embedding(
            embedding=embedding,
            limit=limit + 1,  # +1 to account for self-match
            threshold=threshold
        )

        # Filter out the source video and transform results
        results = []
        for r in raw_results:
            if r["video_id"] != video_id:
                results.append(SearchResult(
                    video_id=r["video_id"],
                    title=r.get("title", ""),
                    description=r.get("description"),
                    cover_url=r.get("cover_url"),
                    similarity=r.get("similarity", 0)
                ))

        results = results[:limit]

        return SearchResponse(
            results=results,
            total=len(results),
            query=f"similar to video {video_id}",
            search_type="similar"
        )

    def _calculate_similarity(
        self,
        embedding1: List[float],
        embedding2_str: str
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
