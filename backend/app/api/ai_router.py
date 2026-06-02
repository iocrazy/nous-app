# backend/app/api/ai_router.py

"""
AI Pipeline API

Endpoints for triggering and retrieving AI analysis results
(transcription, summary, visual analysis).

Supports both platform_id-based (legacy) and resource_id-based triggers.
"""


from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user
from app.db.supabase_client import get_async_supabase_admin as _get_admin
from app.repositories.ai_repository import AIRepository
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.ai import (
    SummaryResponse,
    TranscriptResponse,
    VisualAnalysisResponse,
)
from app.services.billing.points_service import PointsService

router = APIRouter(prefix="/ai", tags=["AI"])


async def _get_media_or_404(platform_id: str) -> dict:
    """Look up media by platform_id or raise 404."""
    repo = MediaRepository()
    media = await repo.get_by_platform_id(platform_id)
    if not media:
        raise HTTPException(status_code=404, detail=f"Media not found: {platform_id}")
    return media


async def _resolve_resource_to_platform_id(resource_id: str) -> tuple[dict, str, dict]:
    """Resolve resource_id -> (resource dict, platform_id, media dict), or raise 404."""
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

    return resource, media["platform_id"], media


def _format_duration_short(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ------------------------------------------------------------------
# Manual triggers (resource_id-based)
# ------------------------------------------------------------------


@router.post("/transcribe/resource/{resource_id}")
async def trigger_transcription_by_resource(resource_id: str, auth: AuthDep):
    """Trigger AI transcription by resource_id."""
    resource, platform_id, media = await _resolve_resource_to_platform_id(resource_id)

    # === Dedup: reject if already processing ===
    _admin = await _get_admin()
    _active = (
        await _admin.table("task_tracking")
        .select("dbos_workflow_id")
        .eq("resource_id", resource_id)
        .eq("task_type", "ai_transcription")
        .in_("status", ["pending", "processing", "running"])
        .limit(1)
        .execute()
    )
    if _active.data:
        return {
            "message": "Transcription already in progress",
            "resource_id": resource_id,
        }
    # === End dedup ===

    # === Nous billing — only charge if user selected a nous-* model ===
    import math

    from app.repositories.nous_repository import NousRepository
    from app.repositories.user_settings_repository import UserSettingsRepository

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
            raise HTTPException(
                status_code=400, detail=f"Nous model '{selected_model}' not available"
            )

        # Compute cost by media duration (reuse media from resolver)
        duration_seconds = float(media.get("duration", 0)) if media else 0
        if duration_seconds <= 0:
            duration_seconds = 60  # fallback: charge 1 minute minimum

        pricing_value = float(nous_model["pricing_value"])
        if nous_model["pricing_type"] == "per_hour":
            _points_cost = max(1, math.ceil(duration_seconds / 3600 * pricing_value))
        else:
            _points_cost = max(1, int(pricing_value))

        # Build detailed description for transaction record
        video_title = (media.get("title") or platform_id)[:50]
        dur_str = (
            _format_duration_short(duration_seconds) if duration_seconds > 0 else ""
        )
        _description = f"AI Transcription: {video_title} ({selected_model}"
        if dur_str:
            _description += f", {dur_str}"
        _description += ")"

        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_transcription",
            reference_id=resource_id,
            override_cost=_points_cost,
            description=_description,
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End billing ===

    # Track unified_task so we can mark it failed if the dispatch
    # itself throws — and so the Task Center sees this run + Realtime
    # pushes status changes back to the frontend.
    _orphan_task_id: str | None = None

    # Audio-readiness gate: the workflow's whisper step needs an audio
    # file on disk. Check the actual on-disk fields, not
    # music_download_status — that one means "music URL was downloaded",
    # which is a different concern from "ffmpeg extracted audio from a
    # video". The MediaCard green icon already uses this same union; the
    # gate is now consistent with what the UI reports.
    if not (
        (media or {}).get("extract_audio_path")
        or (media or {}).get("music_download_path")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Audio not yet extracted for this resource. "
                "Wait for download/extraction to complete, then retry."
            ),
        )

    try:
        # Manual click path = always dispatch transcription, do NOT go
        # through tag-driven `maybe_chain_ai_pipeline` (that helper is
        # for the post-download auto-chain).
        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.ai_transcription import ai_transcription_workflow

        tracker = get_task_manager()
        wf_id = str(_uuid.uuid4())
        _orphan_task_id = await tracker.create(
            user_id=auth.user_id,
            task_type="ai_transcription",
            title=f"Transcribe: {platform_id}",
            media_id=platform_id,
            resource_id=resource_id,
            dbos_workflow_id=wf_id,
        )

        await start_workflow_routed(
            "ai_transcription",
            dbos_workflow_callable=ai_transcription_workflow,
            dbos_workflow_kwargs={
                "parsed_media_id": int(media["id"]),
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
        _orphan_task_id = None
    except HTTPException:
        raise
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
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
    resource, platform_id, media = await _resolve_resource_to_platform_id(resource_id)

    # === Dedup: reject if already processing ===
    _admin = await _get_admin()
    _active = (
        await _admin.table("task_tracking")
        .select("dbos_workflow_id")
        .eq("resource_id", resource_id)
        .eq("task_type", "ai_summary")
        .in_("status", ["pending", "processing", "running"])
        .limit(1)
        .execute()
    )
    if _active.data:
        return {"message": "Summary already in progress", "resource_id": resource_id}
    # === End dedup ===

    # === Points check — charge the resource owner's personal team ===
    points_service = PointsService()
    resource_owner = resource.get("creator_id") or auth.user_id
    _team_id = await get_team_id_for_user(resource_owner)
    _points_cost = 0
    if _team_id:
        # Build detailed description for transaction record
        video_title = (media.get("title") or platform_id)[:50]
        _description = f"AI Summary: {video_title}"

        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_summary",
            reference_id=resource_id,
            description=_description,
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(resource_id)

    # Track unified_task so we can mark it failed if the Celery dispatch
    # itself throws — otherwise the row is orphaned in "processing" until
    # the reaper catches it (which logs "Stale task timeout", masking the
    # real error).
    _orphan_task_id: str | None = None

    try:
        if transcript and transcript.get("full_text"):
            # Transcript exists, dispatch ai_summary_workflow.
            import uuid as _uuid

            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.services.infra.unified_task_manager import get_task_manager
            from app.workflows.ai_summary import ai_summary_workflow

            tracker = get_task_manager()
            wf_id = str(_uuid.uuid4())
            task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="ai_summary",
                title=f"Summarize: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                dbos_workflow_id=wf_id,
            )
            _orphan_task_id = task_id

            await start_workflow_routed(
                "ai_summary",
                dbos_workflow_callable=ai_summary_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media["id"]),
                    "user_id": auth.user_id,
                },
                workflow_id=wf_id,
            )
            _orphan_task_id = None
            return {
                "message": "Summary generation queued",
                "resource_id": resource_id,
                "platform_id": platform_id,
            }
        else:
            # No transcript yet — dispatch transcription. PR-D7 phase
            # 3b: chain_ai_pipeline used to celery-chain transcribe →
            # summary. With DBOS, summary needs a parent workflow to
            # depend on transcript completion. For now we dispatch
            # transcription only; the user re-triggers summary once
            # the transcript lands (frontend polls).
            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.ai_transcription import ai_transcription_workflow

            await start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media["id"]),
                    "user_id": auth.user_id,
                },
            )
            return {
                "message": "Transcription queued; trigger summary again once transcript is ready",
                "resource_id": resource_id,
                "platform_id": platform_id,
            }
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
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
    """Manually trigger L1 cover analysis (analyze_l1_workflow).

    Mirrors the trigger_summary_by_resource pattern: dedup → pre-create
    task_tracking → dispatch DBOS workflow with the same workflow_id so
    the Task Center sees the run and Realtime pushes lifecycle changes
    back to the frontend (otherwise a click would be invisible)."""
    resource, platform_id, media = await _resolve_resource_to_platform_id(resource_id)

    # Dedup: skip if a run is already in flight for this resource.
    _admin = await _get_admin()
    _active = (
        await _admin.table("task_tracking")
        .select("dbos_workflow_id")
        .eq("resource_id", resource_id)
        .eq("task_type", "ai_extract")
        .in_("status", ["pending", "processing", "running"])
        .limit(1)
        .execute()
    )
    if _active.data:
        return {
            "message": "Visual analysis already in progress",
            "resource_id": resource_id,
        }

    cover_url = (media or {}).get("cover_urls") or []
    cover_url = cover_url[0] if cover_url else None
    if not cover_url:
        raise HTTPException(
            status_code=400,
            detail="No cover image to analyze for this resource",
        )

    _orphan_task_id: str | None = None
    try:
        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.analyze_l1 import analyze_l1_workflow

        tracker = get_task_manager()
        wf_id = str(_uuid.uuid4())
        _orphan_task_id = await tracker.create(
            user_id=auth.user_id,
            task_type="ai_extract",
            title=f"Analyze: {(media or {}).get('title') or platform_id}",
            subtitle="L1 cover analysis",
            media_id=platform_id,
            resource_id=resource_id,
            dbos_workflow_id=wf_id,
        )

        await start_workflow_routed(
            "ai_extract",
            dbos_workflow_callable=analyze_l1_workflow,
            dbos_workflow_kwargs={
                "media_id": int(media["id"]),
                "cover_url": cover_url,
                "title": (media or {}).get("title") or "",
                "description": (media or {}).get("description") or "",
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
        _orphan_task_id = None
        return {
            "message": "Visual analysis queued",
            "resource_id": resource_id,
            "platform_id": platform_id,
        }
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
        raise HTTPException(
            status_code=500, detail=f"Failed to queue visual analysis: {str(e)}"
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

    # Audio-readiness gate: same union check as the resource_id endpoint
    # above — extract_audio_path (ffmpeg-extracted) OR music_download_path
    # (URL-downloaded music). Matches the MediaCard green icon.
    _media_check = await _get_media_or_404(platform_id)
    if not (
        (_media_check or {}).get("extract_audio_path")
        or (_media_check or {}).get("music_download_path")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Audio not yet extracted for this resource. "
                "Wait for download/extraction to complete, then retry."
            ),
        )

    # Track unified_task so the Task Center sees this run + Realtime
    # pushes status changes back to the frontend (manual click would
    # otherwise be invisible — the bug behind the "I clicked but
    # nothing showed up" report).
    _orphan_task_id: str | None = None

    try:
        # PR-D7 phase 3b: dispatch ai_transcription_workflow directly.
        # Workflow takes parsed_media_id (int), so look it up.
        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.ai_transcription import ai_transcription_workflow

        media_row = await _get_media_or_404(platform_id)
        # Lookup the user's resource for this platform_id (best-effort —
        # transcription can run without resource_id, the task_tracking
        # row just won't link back to a card).
        from app.repositories.resources_repository import ResourcesRepository

        owner_resource = (
            await ResourcesRepository().get_resource_by_media_id_and_creator(
                str(media_row["id"]), auth.user_id
            )
        )
        owner_resource_id = str(owner_resource["id"]) if owner_resource else None

        tracker = get_task_manager()
        wf_id = str(_uuid.uuid4())
        _orphan_task_id = await tracker.create(
            user_id=auth.user_id,
            task_type="ai_transcription",
            title=f"Transcribe: {platform_id}",
            media_id=platform_id,
            resource_id=owner_resource_id,
            dbos_workflow_id=wf_id,
        )

        await start_workflow_routed(
            "ai_transcription",
            dbos_workflow_callable=ai_transcription_workflow,
            dbos_workflow_kwargs={
                "parsed_media_id": int(media_row["id"]),
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
        _orphan_task_id = None
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
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
    resource = await res_repo.get_resource_by_media_id_and_creator(
        media_id, auth.user_id
    )
    _resource_id = str(resource["id"]) if resource else None

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(_resource_id) if _resource_id else None

    _orphan_task_id: str | None = None

    try:
        if transcript and transcript.get("full_text"):
            # Transcript exists — dispatch ai_summary_workflow.
            import uuid as _uuid

            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.services.infra.unified_task_manager import get_task_manager
            from app.workflows.ai_summary import ai_summary_workflow

            tracker = get_task_manager()
            wf_id = str(_uuid.uuid4())
            task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="ai_summary",
                title=f"Summarize: {platform_id}",
                media_id=platform_id,
                resource_id=_resource_id,
                dbos_workflow_id=wf_id,
            )
            _orphan_task_id = task_id

            await start_workflow_routed(
                "ai_summary",
                dbos_workflow_callable=ai_summary_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media_id),
                    "user_id": auth.user_id,
                },
                workflow_id=wf_id,
            )
            _orphan_task_id = None
            return {"message": "Summary generation queued", "platform_id": platform_id}
        else:
            # No transcript yet — dispatch transcription only. PR-D7
            # phase 3b: see trigger_summary_by_resource for rationale.
            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.ai_transcription import ai_transcription_workflow

            await start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media_id),
                    "user_id": auth.user_id,
                },
            )
            return {
                "message": "Transcription queued; trigger summary again once transcript is ready",
                "platform_id": platform_id,
            }
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
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
    """Get transcript for a resource.

    "Resource not transcribed yet" returns 200 with full_text=None — NOT
    404. Returning 404 makes Chrome paint the entire request red in
    DevTools every time the user opens the Transcript tab on an
    un-transcribed video, even though the UI handles it correctly. The
    poll helper looks for full_text presence, so it keeps polling on
    null and stops on a real transcript.
    """
    # Ownership check
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(resource_id) or {}

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
    resource = await res_repo.get_resource_by_media_id_and_creator(
        media_id, auth.user_id
    )
    if not resource:
        raise HTTPException(status_code=404, detail="No resource linked to this media")

    ai_repo = AIRepository()
    transcript = await ai_repo.get_transcript(str(resource["id"])) or {}

    # 200 + nulls (not 404) when no transcript exists yet — same reason
    # as the by-resource endpoint above. Stops Chrome from painting the
    # request red on every Transcript tab open.
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
    summary = await ai_repo.get_summary(resource_id) or {}

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


@router.get("/analysis/resource/{resource_id}", response_model=VisualAnalysisResponse)
async def get_analysis_by_resource(resource_id: str, auth: AuthDep):
    """Get the L1 visual (cover) analysis for a resource.

    Returns 200 with null fields (NOT 404) when the resource hasn't been
    analyzed yet — same rationale as the transcript/summary endpoints: avoids a
    red request in DevTools on every tab open, and lets the frontend detect
    presence via `visual_description`. The analysis row is keyed by the linked
    media id (parsed_media.id), so resolve resource → media first.
    """
    resource, _platform_id, media = await _resolve_resource_to_platform_id(resource_id)
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    analysis = await AnalysisRepository().get_analysis(int(media["id"])) or {}

    return VisualAnalysisResponse(
        media_id=str(media["id"]),
        analysis_level=analysis.get("analysis_level"),
        visual_description=analysis.get("visual_description"),
        detected_objects=analysis.get("detected_objects"),
        detected_scenes=analysis.get("detected_scenes"),
        detected_people=analysis.get("detected_people"),
        detected_text=analysis.get("detected_text"),
        analysis_model=analysis.get("analysis_model"),
        analysis_cost=analysis.get("analysis_cost"),
        analyzed_at=analysis.get("analyzed_at"),
    )


@router.get("/summary/{platform_id}", response_model=SummaryResponse)
async def get_summary(platform_id: str, auth: AuthDep):
    """Get summary for a media item (legacy, resolves resource from media)."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    # Resolve resource_id from media (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(
        media_id, auth.user_id
    )
    if not resource:
        raise HTTPException(status_code=404, detail="No resource linked to this media")

    ai_repo = AIRepository()
    summary = await ai_repo.get_summary(str(resource["id"])) or {}

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
