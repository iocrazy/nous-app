# backend/app/api/ai_router.py

"""
AI Pipeline API

Endpoints for triggering and retrieving AI analysis results
(transcription, summary, visual analysis).
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.ai_repository import AIRepository
from app.repositories.media_repository import MediaRepository
from app.schemas.ai import SummaryResponse, TranscriptResponse
from app.services.points_service import PointsService

router = APIRouter(prefix="/ai", tags=["AI"])


async def _get_media_or_404(platform_id: str) -> dict:
    """Look up media by platform_id or raise 404."""
    repo = MediaRepository()
    media = await repo.get_by_platform_id(platform_id)
    if not media:
        raise HTTPException(status_code=404, detail=f"Media not found: {platform_id}")
    return media


# ------------------------------------------------------------------
# Manual triggers
# ------------------------------------------------------------------


@router.post("/transcribe/{platform_id}")
async def trigger_transcription(platform_id: str, auth: AuthDep):
    """Manually trigger transcription for a video.

    Queues the extract_audio → transcribe chain via Celery.
    """
    await _get_media_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _admin = await get_async_supabase_admin()
    _tm = (
        await _admin.table("team_members")
        .select("team_id")
        .eq("user_id", auth.user_id)
        .limit(1)
        .execute()
    )
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    _points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_transcription",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    try:
        from app.tasks.ai_tasks import chain_ai_pipeline

        chain_ai_pipeline(
            platform_id=platform_id,
            user_id=auth.user_id,
            transcript_bool=True,
            summary_bool=False,
        )
    except Exception as e:
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="ai_transcription",
                    reference_id=platform_id,
                    reason=f"Task dispatch failed: {str(e)[:100]}",
                )
                logger.info(
                    f"Refunded {_points_cost} points for failed transcription dispatch"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue transcription: {str(e)}"
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
    _tm = (
        await _admin.table("team_members")
        .select("team_id")
        .eq("user_id", auth.user_id)
        .limit(1)
        .execute()
    )
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    _points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_summary",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(media_id)

    try:
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
    except Exception as e:
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="ai_summary",
                    reference_id=platform_id,
                    reason=f"Task dispatch failed: {str(e)[:100]}",
                )
                logger.info(
                    f"Refunded {_points_cost} points for failed summary dispatch"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue summary: {str(e)}"
        )


@router.post("/analyze/{platform_id}")
async def trigger_visual_analysis(platform_id: str, auth: AuthDep):
    """Manually trigger visual analysis for a video.

    (Placeholder - visual analysis is not yet implemented.)
    """
    await _get_media_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _admin = await get_async_supabase_admin()
    _tm = (
        await _admin.table("team_members")
        .select("team_id")
        .eq("user_id", auth.user_id)
        .limit(1)
        .execute()
    )
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    _points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_visual_analysis",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    # Visual analysis is not yet implemented — refund consumed points
    if _points_cost > 0 and _team_id:
        try:
            await points_service.refund_points(
                team_id=_team_id,
                user_id=auth.user_id,
                amount=_points_cost,
                reference_type="ai_visual_analysis",
                reference_id=platform_id,
                reason="Visual analysis not yet implemented",
            )
        except Exception as refund_err:
            logger.error(f"Failed to refund points: {refund_err}")

    raise HTTPException(
        status_code=501,
        detail="Visual analysis is not yet implemented",
    )


# ------------------------------------------------------------------
# Data retrieval
# ------------------------------------------------------------------


@router.get("/transcript/{platform_id}", response_model=TranscriptResponse)
async def get_transcript(platform_id: str, auth: AuthDep):
    """Get transcript for a media item."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(media_id)

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return TranscriptResponse(
        video_id=media_id,
        language=transcript.get("language"),
        full_text=transcript.get("full_text"),
        segments=transcript.get("segments"),
        whisper_model=transcript.get("whisper_model"),
        duration_seconds=transcript.get("duration_seconds"),
        created_at=transcript.get("created_at"),
    )


@router.get("/summary/{platform_id}", response_model=SummaryResponse)
async def get_summary(platform_id: str, auth: AuthDep):
    """Get summary for a media item."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    ai_repo = AIRepository()
    summary = await ai_repo.get_summary(media_id)

    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    return SummaryResponse(
        video_id=media_id,
        summary_type=summary.get("summary_type"),
        summary_text=summary.get("summary_text"),
        key_points=summary.get("key_points"),
        topics=summary.get("topics"),
        llm_model=summary.get("llm_model"),
        llm_provider=summary.get("llm_provider"),
        created_at=summary.get("created_at"),
    )
