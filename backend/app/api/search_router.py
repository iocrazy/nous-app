"""API routes for Semantic Search."""
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.services.search_service import SearchService
from app.schemas.search import (
    SemanticSearchRequest,
    HybridSearchRequest,
    SimilarVideosRequest,
    SearchResponse,
    SearchResultItem,
)


router = APIRouter(prefix="/search", tags=["Search"])


@router.post("/semantic", response_model=SearchResponse)
async def semantic_search(
    request: SemanticSearchRequest,
    auth: AuthDep = None,
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
            user_id=auth.user_id
        )

        return SearchResponse(
            results=[
                SearchResultItem(
                    video_id=r.video_id,
                    title=r.title,
                    description=r.description,
                    cover_url=r.cover_url,
                    similarity=round(r.similarity, 4),
                    tags=r.tags,
                    author=r.author,
                    created_at=r.created_at
                )
                for r in response.results
            ],
            total=response.total,
            query=response.query,
            search_type=response.search_type
        )

    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )


@router.post("/hybrid", response_model=SearchResponse)
async def hybrid_search(
    request: HybridSearchRequest,
    auth: AuthDep = None,
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
            user_id=auth.user_id
        )

        return SearchResponse(
            results=[
                SearchResultItem(
                    video_id=r.video_id,
                    title=r.title,
                    description=r.description,
                    cover_url=r.cover_url,
                    similarity=round(r.similarity, 4),
                    tags=r.tags,
                    author=r.author,
                    created_at=r.created_at
                )
                for r in response.results
            ],
            total=response.total,
            query=response.query,
            search_type=response.search_type
        )

    except Exception as e:
        logger.error(f"Hybrid search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )


@router.get("/similar/{video_id}", response_model=SearchResponse)
async def find_similar_videos(
    video_id: int,
    limit: int = Query(10, ge=1, le=50, description="Maximum number of similar videos"),
    threshold: float = Query(0.6, ge=0.0, le=1.0, description="Minimum similarity threshold"),
    auth: AuthDep = None,
):
    """
    Find videos similar to a given video.

    Uses the video's embedding to find semantically similar content.
    The source video is excluded from results.
    """
    search_service = SearchService()

    try:
        response = await search_service.find_similar_videos(
            video_id=video_id,
            limit=limit,
            threshold=threshold
        )

        if response.total == 0:
            # Check if video exists and has embedding
            from app.repositories.analysis_repository import AnalysisRepository
            analysis_repo = AnalysisRepository()
            analysis = await analysis_repo.get_analysis(video_id)

            if not analysis:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Video not found or not analyzed yet. Run L1 analysis first."
                )

            if not analysis.get("content_embedding"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Video has no embedding. Analysis may be incomplete."
                )

        return SearchResponse(
            results=[
                SearchResultItem(
                    video_id=r.video_id,
                    title=r.title,
                    description=r.description,
                    cover_url=r.cover_url,
                    similarity=round(r.similarity, 4),
                    tags=r.tags,
                    author=r.author,
                    created_at=r.created_at
                )
                for r in response.results
            ],
            total=response.total,
            query=response.query,
            search_type=response.search_type
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Similar videos search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )


@router.get("/quick")
async def quick_search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    auth: AuthDep = None,
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
            user_id=auth.user_id
        )

        # Return simplified format for quick display
        return {
            "results": [
                {
                    "video_id": r.video_id,
                    "title": r.title,
                    "cover_url": r.cover_url,
                    "similarity": round(r.similarity, 2)
                }
                for r in response.results
            ],
            "total": response.total
        }

    except Exception as e:
        logger.error(f"Quick search failed: {e}")
        return {"results": [], "total": 0}
