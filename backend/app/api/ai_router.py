# backend/app/api/ai_router.py

"""
AI Pipeline API

Endpoints for triggering and retrieving AI analysis results
(transcription, summary, visual analysis).

Supports both platform_id-based (legacy) and resource_id-based triggers.
"""

import asyncio

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user
from app.repositories.ai_repository import AIRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
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


async def _resolve_resource_to_platform_id(resource_id: str) -> tuple[dict, str]:
    """Resolve resource_id -> resource dict + platform_id, or raise 404."""
    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource or not resource.get("media_id"):
        raise HTTPException(
            status_code=404,
            detail="Resource not found or has no linked media",
        )

    media_repo = MediaRepository()
    media = await media_repo.get_by_id(resource["media_id"])
    if not media:
        raise HTTPException(status_code=404, detail="Linked media not found")

    return resource, media["platform_id"]


# ------------------------------------------------------------------
# Manual triggers (resource_id-based)
# ------------------------------------------------------------------


@router.post("/transcribe/resource/{resource_id}")
async def trigger_transcription_by_resource(resource_id: str, auth: AuthDep):
    """Trigger AI transcription by resource_id."""
    resource, platform_id = await _resolve_resource_to_platform_id(resource_id)

    # === Nous billing — only charge if user selected a nous-* model ===
    from app.repositories.user_settings_repository import UserSettingsRepository
    from app.repositories.nous_repository import NousRepository
    import math

    settings_repo = UserSettingsRepository()
    user_settings = await settings_repo.get_by_user_id(auth.user_id)
    ai_settings = (user_settings or {}).get("settings_json", {}).get("ai_settings", {})
    selected_model = ai_settings.get("task_assignment", {}).get("transcription", "")

    points_service = PointsService()
    resource_owner = resource.get("creator_id") or auth.user_id
    _team_id = await get_team_id_for_user(resource_owner)
    _points_cost = 0
    _is_nous = selected_model.startswith("nous-")

    if _is_nous and _team_id:
        # Look up Nous model pricing
        nous_repo = NousRepository()
        nous_model = await nous_repo.get_by_name(selected_model)
        if not nous_model or not nous_model.get("is_enabled"):
            raise HTTPException(status_code=400, detail=f"Nous model '{selected_model}' not available")

        # Compute cost by media duration
        media_repo = MediaRepository()
        media = await media_repo.get_by_platform_id(platform_id)
        duration_seconds = float(media.get("duration", 0)) if media else 0
        if duration_seconds <= 0:
            duration_seconds = 60  # fallback: charge 1 minute minimum

        pricing_value = float(nous_model["pricing_value"])
        if nous_model["pricing_type"] == "per_hour":
            _points_cost = max(1, math.ceil(duration_seconds / 3600 * pricing_value))
        else:
            _points_cost = max(1, int(pricing_value))

        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_transcription",
            reference_id=resource_id,
            override_cost=_points_cost,
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End billing ===

    try:
        from app.tasks.ai_tasks import chain_ai_pipeline

        await asyncio.to_thread(chain_ai_pipeline,
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=resource_id,
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
                    reference_id=resource_id,
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

    return {
        "message": "Transcription queued",
        "resource_id": resource_id,
        "platform_id": platform_id,
        "points_charged": _points_cost,
    }


@router.post("/summarize/resource/{resource_id}")
async def trigger_summary_by_resource(resource_id: str, auth: AuthDep):
    """Trigger AI summary by resource_id."""
    resource, platform_id = await _resolve_resource_to_platform_id(resource_id)

    # === Points check — charge the resource owner's personal team ===
    points_service = PointsService()
    resource_owner = resource.get("creator_id") or auth.user_id
    _team_id = await get_team_id_for_user(resource_owner)
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

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(resource_id)

    try:
        if transcript and transcript.get("full_text"):
            # Transcript exists, just run summary
            from app.tasks.ai_tasks import generate_summary_task

            await asyncio.to_thread(generate_summary_task.delay, platform_id, auth.user_id, resource_id)
            return {
                "message": "Summary generation queued",
                "resource_id": resource_id,
                "platform_id": platform_id,
            }
        else:
            # No transcript, run full pipeline
            from app.tasks.ai_tasks import chain_ai_pipeline

            await asyncio.to_thread(chain_ai_pipeline,
                platform_id=platform_id,
                user_id=auth.user_id,
                resource_id=resource_id,
                transcript_bool=True,
                summary_bool=True,
            )
            return {
                "message": "Full AI pipeline queued (transcribe + summarize)",
                "resource_id": resource_id,
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
                    reference_id=resource_id,
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


@router.post("/analyze/resource/{resource_id}")
async def trigger_visual_analysis_by_resource(resource_id: str, auth: AuthDep):
    """Trigger visual analysis by resource_id (not yet implemented)."""
    resource, platform_id = await _resolve_resource_to_platform_id(resource_id)

    raise HTTPException(
        status_code=501,
        detail="Visual analysis is not yet implemented",
    )


# ------------------------------------------------------------------
# Manual triggers (platform_id-based, legacy)
# ------------------------------------------------------------------


@router.post("/transcribe/{platform_id}")
async def trigger_transcription(platform_id: str, auth: AuthDep):
    """Manually trigger transcription for a video (legacy, platform_id-based).

    Queues the extract_audio -> transcribe chain via Celery.
    """
    await _get_media_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _team_id = await get_team_id_for_user(auth.user_id)
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

        await asyncio.to_thread(chain_ai_pipeline,
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=None,
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
    """Manually trigger summary generation for a video (legacy, platform_id-based).

    Requires an existing transcript. If no transcript exists,
    queues the full pipeline (extract -> transcribe -> summarize).
    """
    # === Points check ===
    points_service = PointsService()
    _team_id = await get_team_id_for_user(auth.user_id)
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

    # Resolve resource_id for per-resource transcript lookup (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(media_id, auth.user_id)
    _resource_id = str(resource["id"]) if resource else None

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(_resource_id) if _resource_id else None

    try:
        if transcript and transcript.get("full_text"):
            # Transcript exists, just run summary
            from app.tasks.ai_tasks import generate_summary_task

            await asyncio.to_thread(generate_summary_task.delay, platform_id, auth.user_id, _resource_id)
            return {"message": "Summary generation queued", "platform_id": platform_id}
        else:
            # No transcript, run full pipeline
            from app.tasks.ai_tasks import chain_ai_pipeline

            await asyncio.to_thread(chain_ai_pipeline,
                platform_id=platform_id,
                user_id=auth.user_id,
                resource_id=_resource_id,
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
    """Manually trigger visual analysis (L1: cover image) for a video.

    Queues L1 analysis via Celery. If a local video file exists,
    L2 analysis (cover + keyframes) is queued instead.
    """
    await _get_media_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _team_id = await get_team_id_for_user(auth.user_id)
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

    # Visual analysis is not yet implemented -- refund consumed points
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


@router.get("/transcript/resource/{resource_id}", response_model=TranscriptResponse)
async def get_transcript_by_resource(resource_id: str, auth: AuthDep):
    """Get transcript for a resource."""
    # Ownership check
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(resource_id)

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return TranscriptResponse(
        media_id=resource_id,
        language=transcript.get("language"),
        full_text=transcript.get("full_text"),
        segments=transcript.get("segments"),
        whisper_model=transcript.get("whisper_model"),
        duration_seconds=transcript.get("duration_seconds"),
        created_at=transcript.get("created_at"),
    )


@router.get("/transcript/{platform_id}", response_model=TranscriptResponse)
async def get_transcript(platform_id: str, auth: AuthDep):
    """Get transcript for a media item (legacy, resolves resource from media)."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    # Resolve resource_id from media (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(media_id, auth.user_id)
    if not resource:
        raise HTTPException(status_code=404, detail="No resource linked to this media")

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(str(resource["id"]))

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return TranscriptResponse(
        media_id=media_id,
        language=transcript.get("language"),
        full_text=transcript.get("full_text"),
        segments=transcript.get("segments"),
        whisper_model=transcript.get("whisper_model"),
        duration_seconds=transcript.get("duration_seconds"),
        created_at=transcript.get("created_at"),
    )


@router.get("/summary/resource/{resource_id}", response_model=SummaryResponse)
async def get_summary_by_resource(resource_id: str, auth: AuthDep):
    """Get summary for a resource."""
    # Ownership check
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    ai_repo = AIRepository()
    summary = await ai_repo.get_summary(resource_id)

    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    return SummaryResponse(
        media_id=resource_id,
        summary_type=summary.get("summary_type"),
        summary_text=summary.get("summary_text"),
        key_points=summary.get("key_points"),
        topics=summary.get("topics"),
        llm_model=summary.get("llm_model"),
        llm_provider=summary.get("llm_provider"),
        created_at=summary.get("created_at"),
    )


@router.get("/summary/{platform_id}", response_model=SummaryResponse)
async def get_summary(platform_id: str, auth: AuthDep):
    """Get summary for a media item (legacy, resolves resource from media)."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    # Resolve resource_id from media (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(media_id, auth.user_id)
    if not resource:
        raise HTTPException(status_code=404, detail="No resource linked to this media")

    ai_repo = AIRepository()
    summary = await ai_repo.get_summary(str(resource["id"]))

    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    return SummaryResponse(
        media_id=media_id,
        summary_type=summary.get("summary_type"),
        summary_text=summary.get("summary_text"),
        key_points=summary.get("key_points"),
        topics=summary.get("topics"),
        llm_model=summary.get("llm_model"),
        llm_provider=summary.get("llm_provider"),
        created_at=summary.get("created_at"),
    )
