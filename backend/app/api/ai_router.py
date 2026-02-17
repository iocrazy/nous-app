# backend/app/api/ai_router.py

"""
AI Pipeline API

Endpoints for triggering and retrieving AI analysis results
(transcription, summary, visual analysis).
"""

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.ai_repository import AIRepository
from app.repositories.video_repository import VideoRepository
from app.schemas.ai import SummaryResponse, TranscriptResponse
from app.services.points_service import PointsService

router = APIRouter(prefix="/ai", tags=["AI"])


async def _get_video_or_404(platform_id: str) -> dict:
    """Look up video by platform_id or raise 404."""
    repo = VideoRepository()
    video = await repo.get_by_platform_id(platform_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Video not found: {platform_id}")
    return video


# ------------------------------------------------------------------
# Manual triggers
# ------------------------------------------------------------------


@router.post("/transcribe/{platform_id}")
async def trigger_transcription(platform_id: str, auth: AuthDep):
    """Manually trigger transcription for a video.

    Queues the extract_audio → transcribe chain via Celery.
    """
    await _get_video_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _admin = await get_async_supabase_admin()
    _tm = await _admin.table("team_members").select("team_id").eq("user_id", auth.user_id).limit(1).execute()
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    if _team_id:
        await points_service.ensure_team_quota(_team_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_transcription",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
    # === End points check ===

    from app.tasks.ai_tasks import chain_ai_pipeline

    chain_ai_pipeline(
        platform_id=platform_id,
        user_id=auth.user_id,
        transcript_bool=True,
        summary_bool=False,
    )

    return {"message": "Transcription queued", "platform_id": platform_id}


@router.post("/summarize/{platform_id}")
async def trigger_summary(platform_id: str, auth: AuthDep):
    """Manually trigger summary generation for a video.

    Requires an existing transcript. If no transcript exists,
    queues the full pipeline (extract → transcribe → summarize).
    """
    # === Points check ===
    points_service = PointsService()
    _admin = await get_async_supabase_admin()
    _tm = await _admin.table("team_members").select("team_id").eq("user_id", auth.user_id).limit(1).execute()
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    if _team_id:
        await points_service.ensure_team_quota(_team_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_summary",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
    # === End points check ===

    video = await _get_video_or_404(platform_id)
    video_id = video["id"]

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(video_id)

    if transcript and transcript.get("full_text"):
        # Transcript exists, just run summary
        from app.tasks.ai_tasks import generate_summary_task

        generate_summary_task.delay(platform_id, auth.user_id)
        return {"message": "Summary generation queued", "platform_id": platform_id}
    else:
        # No transcript, run full pipeline
        from app.tasks.ai_tasks import chain_ai_pipeline

        chain_ai_pipeline(
            platform_id=platform_id,
            user_id=auth.user_id,
            transcript_bool=True,
            summary_bool=True,
        )
        return {
            "message": "Full AI pipeline queued (transcribe + summarize)",
            "platform_id": platform_id,
        }


@router.post("/analyze/{platform_id}")
async def trigger_visual_analysis(platform_id: str, auth: AuthDep):
    """Manually trigger visual analysis (L1: cover image) for a video.

    Queues L1 analysis via Celery. If a local video file exists,
    L2 analysis (cover + keyframes) is queued instead.
    """
    video = await _get_video_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _admin = await get_async_supabase_admin()
    _tm = await _admin.table("team_members").select("team_id").eq("user_id", auth.user_id).limit(1).execute()
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    if _team_id:
        await points_service.ensure_team_quota(_team_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_visual_analysis",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
    # === End points check ===

    video_id = video["id"]
    cover_urls = video.get("cover_urls") or []
    cover_url = cover_urls[0] if cover_urls else ""
    if not cover_url:
        raise HTTPException(status_code=400, detail="Video has no cover image for analysis")

    title = video.get("title", "")
    description = video.get("description", "")
    download_path = video.get("download_path")

    # Update status to processing
    repo = VideoRepository()
    await repo.update(platform_id, {"visual_analysis_status": "processing"})

    from app.tasks.analysis_tasks import analyze_video_l1_task, analyze_video_l2_task

    if download_path:
        # L2: cover + keyframes (richer analysis)
        analyze_video_l2_task.delay(
            video_id=video_id,
            cover_url=cover_url,
            video_path=download_path,
            title=title,
            description=description,
        )
        return {
            "message": "L2 visual analysis queued (cover + keyframes)",
            "platform_id": platform_id,
            "level": "L2",
        }
    else:
        # L1: cover only
        analyze_video_l1_task.delay(
            video_id=video_id,
            cover_url=cover_url,
            title=title,
            description=description,
        )
        return {
            "message": "L1 visual analysis queued (cover image)",
            "platform_id": platform_id,
            "level": "L1",
        }


# ------------------------------------------------------------------
# Data retrieval
# ------------------------------------------------------------------


@router.get("/transcript/{platform_id}", response_model=TranscriptResponse)
async def get_transcript(platform_id: str, auth: AuthDep):
    """Get transcript for a video."""
    video = await _get_video_or_404(platform_id)
    video_id = video["id"]

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(video_id)

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return TranscriptResponse(
        video_id=video_id,
        language=transcript.get("language"),
        full_text=transcript.get("full_text"),
        segments=transcript.get("segments"),
        whisper_model=transcript.get("whisper_model"),
        duration_seconds=transcript.get("duration_seconds"),
        created_at=transcript.get("created_at"),
    )


@router.get("/summary/{platform_id}", response_model=SummaryResponse)
async def get_summary(platform_id: str, auth: AuthDep):
    """Get summary for a video."""
    video = await _get_video_or_404(platform_id)
    video_id = video["id"]

    ai_repo = AIRepository()
    summary = await ai_repo.get_summary(video_id)

    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    return SummaryResponse(
        video_id=video_id,
        summary_type=summary.get("summary_type"),
        summary_text=summary.get("summary_text"),
        key_points=summary.get("key_points"),
        topics=summary.get("topics"),
        llm_model=summary.get("llm_model"),
        llm_provider=summary.get("llm_provider"),
        created_at=summary.get("created_at"),
    )
