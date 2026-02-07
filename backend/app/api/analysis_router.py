"""API routes for Video Analysis."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.repositories.analysis_repository import AnalysisRepository
from app.tasks.analysis_tasks import (
    analyze_pending_videos_task,
    analyze_video_l1_task,
    analyze_video_l2_task,
    batch_analyze_l1_task,
)

router = APIRouter(prefix="/analysis", tags=["Analysis"])


# Request/Response schemas
class AnalysisResponse(BaseModel):
    """Response schema for video analysis."""

    video_id: int
    analysis_level: str
    visual_description: Optional[str] = None
    detected_objects: List[str] = []
    detected_scenes: List[str] = []
    detected_people: List[dict] = []
    detected_text: Optional[str] = None
    analysis_model: Optional[str] = None
    analysis_cost: float = 0.0
    analyzed_at: Optional[str] = None


class AnalysisStatsResponse(BaseModel):
    """Response schema for analysis statistics."""

    total_videos: int
    analyzed_videos: int
    by_level: dict
    total_cost: float


class AnalyzeRequest(BaseModel):
    """Request schema for triggering analysis."""

    level: str = "L1"  # L1, L2, or L3


class BatchAnalyzeRequest(BaseModel):
    """Request schema for batch analysis."""

    video_ids: List[int]
    level: str = "L1"


class TaskStatusResponse(BaseModel):
    """Response schema for async task status."""

    message: str
    task_id: str
    video_id: Optional[int] = None


@router.get("/stats", response_model=AnalysisStatsResponse)
async def get_analysis_stats(auth: AuthDep = None):
    """
    Get analysis statistics.
    Shows counts by analysis level and total cost.
    """
    from app.db.supabase_client import get_async_supabase_admin

    supabase = await get_async_supabase_admin()

    # Total videos
    total_result = await supabase.table("videos").select("id", count="exact").execute()
    total_videos = total_result.count or 0

    # Analyzed videos
    analysis_result = (
        await supabase.table("video_analysis")
        .select("analysis_level, analysis_cost")
        .execute()
    )

    analyzed_videos = len(analysis_result.data)

    # Group by level
    by_level = {"none": 0, "L1": 0, "L2": 0, "L3": 0}
    total_cost = 0.0

    for item in analysis_result.data:
        level = item.get("analysis_level", "none")
        by_level[level] = by_level.get(level, 0) + 1
        total_cost += item.get("analysis_cost", 0) or 0

    by_level["none"] = total_videos - analyzed_videos

    return AnalysisStatsResponse(
        total_videos=total_videos,
        analyzed_videos=analyzed_videos,
        by_level=by_level,
        total_cost=round(total_cost, 4),
    )


@router.get("/queue")
async def get_analysis_queue(
    limit: int = Query(
        50, le=100, description="Maximum number of pending videos to return"
    ),
    auth: AuthDep = None,
):
    """
    Get videos pending analysis.
    Returns videos that don't have any analysis yet.
    """
    repo = AnalysisRepository()
    videos = await repo.get_videos_without_analysis(limit=limit)

    return {"pending_count": len(videos), "videos": videos}


@router.get("/{video_id}", response_model=AnalysisResponse)
async def get_video_analysis(
    video_id: int,
    auth: AuthDep = None,
):
    """
    Get analysis results for a video.
    Returns 404 if no analysis exists for the video.
    """
    repo = AnalysisRepository()
    analysis = await repo.get_analysis(video_id)

    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found for this video",
        )

    return AnalysisResponse(
        video_id=analysis["video_id"],
        analysis_level=analysis.get("analysis_level", "none"),
        visual_description=analysis.get("visual_description"),
        detected_objects=analysis.get("detected_objects", []),
        detected_scenes=analysis.get("detected_scenes", []),
        detected_people=analysis.get("detected_people", []),
        detected_text=analysis.get("detected_text"),
        analysis_model=analysis.get("analysis_model"),
        analysis_cost=analysis.get("analysis_cost", 0),
        analyzed_at=analysis.get("analyzed_at"),
    )


@router.post(
    "/{video_id}/analyze",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_analysis(
    video_id: int,
    request: AnalyzeRequest,
    auth: AuthDep = None,
):
    """
    Trigger analysis for a video.

    Analysis levels:
    - **L1**: Cover image analysis (fast, ~$0.001)
    - **L2**: Cover + keyframes analysis (requires downloaded video, ~$0.005)
    - **L3**: Full video analysis (manual, ~$0.05) - not yet implemented
    """
    from app.core.utils import Utils
    from app.db.supabase_client import get_async_supabase_admin

    supabase = await get_async_supabase_admin()

    # Get video info
    result = (
        await supabase.table("videos")
        .select("id, title, description, cover_url, download_path")
        .eq("id", video_id)
        .maybe_single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Video not found"
        )

    video = result.data

    if request.level == "L1":
        if not video.get("cover_url"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Video has no cover URL"
            )

        task = analyze_video_l1_task.delay(
            video_id=video_id,
            cover_url=video["cover_url"],
            title=video.get("title", ""),
            description=video.get("description", ""),
        )

        return TaskStatusResponse(
            message="L1 analysis started", task_id=task.id, video_id=video_id
        )

    elif request.level == "L2":
        if not video.get("download_path"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Video file not downloaded yet. Download the video first, then run L2 analysis.",
            )

        # Get full path
        try:
            base_path = Utils.get_download_base_path()
            video_path = f"{base_path}/{video['download_path']}"
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Download path not configured: {e}",
            )

        task = analyze_video_l2_task.delay(
            video_id=video_id,
            cover_url=video.get("cover_url", ""),
            video_path=video_path,
            title=video.get("title", ""),
            description=video.get("description", ""),
        )

        return TaskStatusResponse(
            message="L2 analysis started", task_id=task.id, video_id=video_id
        )

    elif request.level == "L3":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="L3 analysis (full video) is not yet implemented",
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid analysis level: {request.level}. Use L1, L2, or L3.",
        )


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
async def trigger_batch_analysis(
    request: BatchAnalyzeRequest,
    auth: AuthDep = None,
):
    """
    Trigger batch analysis for multiple videos.
    Currently only supports L1 analysis.
    """
    if len(request.video_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 videos per batch",
        )

    if request.level != "L1":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch analysis currently only supports L1 level",
        )

    task = batch_analyze_l1_task.delay(
        video_ids=request.video_ids, batch_size=len(request.video_ids)
    )

    return {
        "message": f"Batch {request.level} analysis started for {len(request.video_ids)} videos",
        "task_id": task.id,
        "video_count": len(request.video_ids),
    }


@router.post("/analyze-pending", status_code=status.HTTP_202_ACCEPTED)
async def analyze_pending_videos(
    limit: int = Query(50, le=100, description="Maximum number of videos to analyze"),
    auth: AuthDep = None,
):
    """
    Analyze all pending videos (videos without analysis).
    Useful for backfilling analysis on existing videos.
    """
    task = analyze_pending_videos_task.delay(limit=limit)

    return {
        "message": f"Started analyzing pending videos (up to {limit})",
        "task_id": task.id,
    }


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_analysis(
    video_id: int,
    auth: AuthDep = None,
):
    """
    Delete analysis for a video.
    Useful for re-analyzing a video.
    """
    repo = AnalysisRepository()
    deleted = await repo.delete_analysis(video_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found for this video",
        )
