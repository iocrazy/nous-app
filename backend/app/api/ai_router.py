# backend/app/api/ai_router.py

"""
AI Pipeline API

Endpoints for triggering and retrieving AI analysis results
(transcription, summary, visual analysis).
"""

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.repositories.ai_repository import AIRepository
from app.repositories.video_repository import VideoRepository
from app.schemas.ai import SummaryResponse, TranscriptResponse

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
    """Manually trigger visual analysis for a video.

    (Placeholder - visual analysis is not yet implemented.)
    """
    await _get_video_or_404(platform_id)

    # Visual analysis is planned for a future iteration
    raise HTTPException(
        status_code=501,
        detail="Visual analysis is not yet implemented",
    )


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
