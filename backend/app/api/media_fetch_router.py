# backend/app/api/media_fetch_router.py

"""
Media Fetch Router

Endpoints for parsing and fetching media (single, batch, per-type, extract-audio).
Helper functions are in media_fetch_helpers.py.
"""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from loguru import logger

from app.core.deps import AuthDep
from app.core.utils import Utils
from app.repositories.user_logs_repository import log_user_action
from app.repositories.media_repository import MediaRepository
from app.services.douyin_parse.formatter import DouyinFormatter
from app.services.douyin_parse.ies_parser import IesDouyinParser
from app.services.points_service import PointsService
from app.services.url_router import URLRouter
from app.schemas.media import MediaTypeFetchRequest
from app.services.media_service import MediaService

from app.api.media_fetch_helpers import (
    MediaFetchRequest,
    resolve_team_id,
    dedup_and_dispatch,
    handle_ytdlp_fetch,
)
from app.api.media_batch_router import router as batch_router

router = APIRouter()
router.include_router(batch_router)

TAGS_FETCH = ["Video Fetch"]


# ============================================
# Route endpoints
# ============================================


@router.post("/fetch", tags=TAGS_FETCH)
async def fetch_video(
    request: MediaFetchRequest, background_tasks: BackgroundTasks, auth: AuthDep,
    raw_request: Request,
):
    """
    Fetch a single video

    Parse video info from a link and automatically download.
    """
    _points_cost = 0
    _team_id = None
    points_service = PointsService()
    try:
        try:
            valid_urls = Utils.extract_valid_url(request.url)
            url = valid_urls[0]
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Cannot extract a valid link from input"
            )

        logger.info(f"[Fetch/Parse] User {auth.user_id} parsing URL, url={url}")

        _team_id = await resolve_team_id(auth.user_id, raw_request)
        if _team_id:
            await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)

            from app.db.supabase_client import get_async_supabase_admin as _get_admin
            _admin = await _get_admin()
            _existing = (
                await _admin.table("parsed_media")
                .select("id")
                .eq("original_url", url)
                .limit(1)
                .execute()
            )

            if not _existing.data:
                points_result = await points_service.check_and_consume(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    action_type="video_parse",
                    reference_id=url,
                )
                if not points_result["success"]:
                    raise HTTPException(status_code=402, detail=points_result["reason"])
                _points_cost = points_result.get("points_cost", 0)
            else:
                logger.info(f"[Fetch/Parse] URL already parsed, skipping points charge")

        platform, handler_type = URLRouter.detect_platform(url)
        logger.info(f"[URLRouter] Platform: {platform}, Handler: {handler_type}")

        return await handle_ytdlp_fetch(
            url=url,
            platform=platform,
            request=request,
            background_tasks=background_tasks,
            auth=auth,
            tags=request.tags,
            tag_ids=request.tag_ids,
        )

    except HTTPException as he:
        if _points_cost > 0 and _team_id and getattr(he, "status_code", 0) != 402:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="video_parse",
                    reason=f"Parse failed: {getattr(he, 'detail', str(he))[:100]}",
                )
                logger.info(f"Refunded {_points_cost} points for failed video parse")
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"Failed to fetch video: {request.url[:30]}...",
            status="error",
            details={"error": he.detail if hasattr(he, "detail") else str(he)},
        )
        raise
    except Exception as e:
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="video_parse",
                    reason=f"Parse failed: {str(e)[:100]}",
                )
                logger.info(f"Refunded {_points_cost} points for failed video parse")
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        logger.error(f"Failed to fetch video: {e}")
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"Failed to fetch video: {str(e)[:50]}",
            status="error",
            details={"error": str(e)[:200]},
        )
        raise HTTPException(status_code=500, detail=f"Failed to fetch video: {str(e)}")


@router.post("/{platform_id}/fetch", tags=TAGS_FETCH)
async def fetch_media_by_type(
    platform_id: str,
    request: MediaTypeFetchRequest,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
):
    """
    Fetch specific media types for an already-parsed video.
    """
    try:
        logger.info(
            f"[Download/Init] User {auth.user_id} requesting {request.types} "
            f"for {platform_id}"
        )

        repo = MediaRepository()
        media = await repo.get_by_platform_id(platform_id)
        if not media:
            raise HTTPException(status_code=404, detail="Media not found. Use POST /videos/fetch first.")

        media_id = media.get("id")
        media_type = media.get("media_type", 0)
        video_title = media.get("title", platform_id)

        from app.repositories.resources_repository import ResourcesRepository
        resources_repo = ResourcesRepository()
        user_resource = await resources_repo.get_resource_by_media_id_and_creator(
            media_id, auth.user_id
        )
        resource_id = user_resource.get("id") if user_resource else None

        if not resource_id:
            resource_id = await MediaService._ensure_user_resource(
                resources_repo=resources_repo,
                media_id=media_id,
                user_id=auth.user_id,
                parsed_data=media,
                need_download_video="video" in request.types or "image" in request.types,
                need_download_music=False,
                need_download_cover="cover" in request.types,
                is_image_type=int(media_type) in (2, 68),
                dedup_hit=False,
                existing_media=media,
            )
        else:
            status_updates = {}
            for t in request.types:
                status_field = f"{t}_download_status"
                if user_resource.get(status_field) == "skipped":
                    status_updates[status_field] = "pending"
            if status_updates:
                await resources_repo.update_download_status(resource_id, status_updates)

        original_url = media.get("original_url")
        dispatch_url = None
        platform = None
        if original_url:
            platform, _ = URLRouter.detect_platform(original_url)
            if platform not in ("douyin", "tiktok"):
                dispatch_url = original_url

        if platform in ("douyin", "tiktok"):
            try:
                aweme_detail = await IesDouyinParser._fetch_share_page(platform_id)
                if aweme_detail:
                    IesDouyinParser._process_video_urls(aweme_detail)
                    new_parsed = await DouyinFormatter.parse_aweme_detail(
                        aweme_detail=aweme_detail,
                        valid_url=media.get("original_url", ""),
                        download_video=True,
                        download_music=True,
                        download_cover=True,
                    )
                    if new_parsed:
                        update_fields = {}
                        for field in ["video_download_urls", "image_download_urls", "music_play_urls", "cover_urls"]:
                            if new_parsed.get(field):
                                update_fields[field] = new_parsed[field]
                        for field in ["like_count", "comment_count", "share_count", "favorite_count"]:
                            if new_parsed.get(field) is not None:
                                update_fields[field] = new_parsed[field]
                        if update_fields:
                            await repo.update(platform_id, update_fields)
                            logger.info(f"[Refetch] Re-parsed {platform_id}: updated {list(update_fields.keys())}")
            except Exception as e:
                logger.warning(f"[Refetch] Re-parse failed for {platform_id}, proceeding with existing URLs: {e}")

        dispatch_result = await dedup_and_dispatch(
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=resource_id,
            media_type=int(media_type) if str(media_type).isdigit() else 0,
            video_title=video_title,
            download_video="video" in request.types or "image" in request.types,
            download_cover="cover" in request.types,
            url=dispatch_url,
            background_tasks=background_tasks,
        )

        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"Fetch {request.types} for {video_title[:30]}...",
            status="success",
            aweme_id=platform_id,
        )

        return {
            "success": True,
            "message": "Fetch submitted",
            "platform_id": platform_id,
            "task_id": dispatch_result.get("unified_task_id") or dispatch_result.get("task_id"),
            "types_submitted": dispatch_result["types_submitted"],
            "types_skipped": dispatch_result["types_skipped"],
            "types_subscribed": dispatch_result["types_subscribed"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Download/Init] Failed: {platform_id}, error: {e}")
        raise HTTPException(status_code=500, detail=f"Fetch failed: {str(e)}")


@router.post("/{platform_id}/extract-audio", tags=TAGS_FETCH)
async def extract_audio(
    platform_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
):
    """
    Re-extract audio from a downloaded video file (ffmpeg -c:a copy).
    """
    try:
        repo = MediaRepository()
        media = await repo.get_by_platform_id(platform_id)
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")

        download_path = media.get("download_path")
        if not download_path:
            raise HTTPException(
                status_code=400,
                detail="No video file found. Download the video first.",
            )

        video_title = media.get("title", platform_id)[:30]

        from app.services.unified_task_manager import get_task_manager
        tracker = get_task_manager()
        unified_task_id = None
        try:
            unified_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="download",
                title=video_title,
                subtitle="Audio Extract",
                media_id=platform_id,
            )
            await tracker.start(unified_task_id)
        except Exception as e:
            logger.warning(f"[ExtractAudio] Failed to create unified task: {e}")

        async def _do_extract(pid: str, task_id: str | None):
            import asyncio
            from app.tasks.download_tasks import _extract_audio_from_video
            _tracker = get_task_manager()
            try:
                success = await asyncio.to_thread(_extract_audio_from_video, pid)
                if task_id:
                    if success:
                        await _tracker.complete(task_id)
                    else:
                        await _tracker.fail(task_id, "Audio extraction failed")
            except Exception as e:
                logger.error(f"[ExtractAudio] Failed for {pid}: {e}")
                if task_id:
                    await _tracker.fail(task_id, str(e)[:500])

        background_tasks.add_task(_do_extract, platform_id, unified_task_id)

        return {
            "success": True,
            "message": "Audio extraction started",
            "platform_id": platform_id,
            "task_id": unified_task_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ExtractAudio] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Extract audio failed: {str(e)}")
