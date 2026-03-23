# backend/app/api/media_router.py

"""
Media Router

Parsed media processing API endpoints based on Supabase.
Requires authentication (JWT or API Key).
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel, field_validator

from app.core.deps import AuthDep, OptionalAuthDep
from app.core.enums import DownloadStatus
from app.repositories.tags_repository import TagsRepository
from app.core.utils import Utils
from app.repositories.user_logs_repository import UserLogsRepository, log_user_action
from app.repositories.user_settings_repository import UserSettingsRepository
from app.repositories.media_repository import MediaRepository
from app.services.douyin_analysis import DouyinAnalysis
from app.services.douyin_parser import DouyinParser
from app.services.lightweight_parser import LightweightParser
from app.services.points_service import PointsService
from app.services.url_router import URLRouter
from app.schemas.media import MediaTypeFetchRequest
from app.services.media_service import MediaService
from app.services.ytdlp_service import YtdlpService

router = APIRouter(prefix="/videos")

# API group tags
TAGS_FETCH = ["Video Fetch"]  # Fetch and parse videos
TAGS_VIDEOS = ["Video Management"]  # CRUD for stored data
TAGS_STATS = ["Statistics"]  # Statistics and analytics
TAGS_DOWNLOAD = ["Download Management"]  # Download operations
TAGS_LOGS = ["Logs"]  # User action logs


async def _resolve_team_id(user_id: str, request: Request) -> Optional[str]:
    """Resolve team_id from X-Team-Id header, falling back to personal team."""
    from app.db.supabase_client import get_async_supabase_admin as _get_admin

    # Prefer explicit header from frontend
    team_id = request.headers.get("X-Team-Id")
    if team_id:
        logger.debug(f"[_resolve_team_id] Using X-Team-Id header: {team_id}")
        return team_id

    # Fallback: prefer personal team, then any team
    admin = await _get_admin()

    # Try personal team first
    personal = (
        await admin.table("teams")
        .select("id")
        .eq("owner_id", user_id)
        .eq("is_personal", True)
        .limit(1)
        .execute()
    )
    if personal.data:
        tid = str(personal.data[0]["id"])
        logger.debug(f"[_resolve_team_id] Fallback to personal team: {tid}")
        return tid

    # Last resort: any team membership
    tm = (
        await admin.table("team_members")
        .select("team_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    tid = str(tm.data[0]["team_id"]) if tm.data else None
    logger.debug(f"[_resolve_team_id] Fallback to first membership: {tid}")
    return tid


# ============================================
# Request/Response models
# ============================================


def _coerce_str_to_list(v):
    """Accept both 'a,b,c' and ['a','b','c']."""
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


class MediaFetchRequest(BaseModel):
    """Video fetch request"""

    url: str
    video_bool: bool = True
    cover_bool: bool = True
    use_celery: bool = False  # Whether to use Celery async tasks
    tags: Optional[list[str]] = None  # Tag names (auto-create if missing)
    tag_ids: Optional[list[str]] = None  # Existing tag UUIDs

    @field_validator("tags", "tag_ids", mode="before")
    @classmethod
    def accept_comma_string(cls, v):
        return _coerce_str_to_list(v) if v is not None else v


class MediaSearchRequest(BaseModel):
    """Video search request"""

    keyword: Optional[str] = None
    author: Optional[str] = None
    status: Optional[str] = None
    media_type: Optional[str] = None
    category: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class BatchFetchRequest(BaseModel):
    """Batch fetch request"""

    urls: list[str]
    video_bool: bool = True
    cover_bool: bool = True
    use_celery: bool = False  # Whether to use Celery async tasks
    tags: Optional[list[str]] = None  # Tag names (auto-create if missing)
    tag_ids: Optional[list[str]] = None  # Existing tag UUIDs

    @field_validator("tags", "tag_ids", mode="before")
    @classmethod
    def accept_comma_string(cls, v):
        return _coerce_str_to_list(v) if v is not None else v


async def _resolve_and_attach_tags(
    resource_id: str, tag_names: list[str], user_id: str
) -> list[str]:
    """Resolve tag names to IDs (auto-create if missing) and attach to resource.

    Returns list of attached tag IDs.
    """
    repo = TagsRepository()
    tag_ids = []
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = await repo.get_tag_by_name(name, user_id)
        if not tag:
            tag = await repo.create_tag(name=name, user_id=user_id)
        tag_ids.append(str(tag["id"]))
    if tag_ids:
        await repo.bulk_add_tags_to_resource(resource_id, tag_ids, source="manual")
    return tag_ids


# ============================================
# Shared helpers
# ============================================


async def _dedup_and_dispatch(
    *,
    platform_id: str,
    user_id: str,
    resource_id: str | None,
    media_type: int,
    video_title: str,
    download_video: bool,
    download_cover: bool,
    url: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    """Per-type Orchestrator dedup check + Celery dispatch.

    Returns:
        {
            "task_id": str | None,
            "types_submitted": [...],
            "types_skipped": [...],
            "types_subscribed": [...],
        }
    """
    from app.services.unified_task_manager import get_task_manager
    from app.tasks.download_tasks import download_unified_task

    is_image_type = int(media_type) in (2, 68)

    # Build requested types list
    requested = {}
    if download_video:
        requested["image" if is_image_type else "video"] = True
    if download_cover:
        requested["cover"] = True

    if not requested:
        return {"task_id": None, "types_submitted": [], "types_skipped": [], "types_subscribed": []}

    # Per-type dedup
    types_to_download = []
    types_subscribed = []
    types_skipped = []

    orchestrator = get_task_manager()
    for dtype in requested:
        try:
            result = await orchestrator.acquire_or_subscribe(
                task_type=f"download:{dtype}",
                dedup_identifier=platform_id,
                user_id=user_id,
                resource_id=resource_id or "",
            )
            action = result["action"]
            logger.info(f"[Download/Dedup] {dtype}={action} for {platform_id}")
            if action == "created":
                types_to_download.append(dtype)
            elif action == "subscribed":
                types_subscribed.append(dtype)
            else:  # completed
                types_skipped.append(dtype)
        except Exception as e:
            logger.warning(f"[Download/Dedup] {dtype} dedup failed, proceeding: {e}")
            types_to_download.append(dtype)

    task_id = None
    unified_task_id = None
    if types_to_download:
        # Map back to download_* bools
        dl_video = ("video" in types_to_download) or ("image" in types_to_download)
        dl_cover = "cover" in types_to_download

        # Pre-create unified_task in HTTP handler (eliminates race window)
        try:
            dl_parts = [t.capitalize() for t in types_to_download]
            dl_subtitle = " + ".join(dl_parts)
            unified_task_id = await orchestrator.create(
                user_id=user_id,
                task_type="download",
                title=video_title[:50] if video_title else platform_id,
                subtitle=dl_subtitle,
                media_id=platform_id,
                resource_id=resource_id,
            )
        except Exception as e:
            logger.warning(f"[Download/Dedup] Pre-create unified_task failed: {e}")

        try:
            logger.info(
                f"[Download/Init] Celery dispatch: platform_id={platform_id}, "
                f"types={types_to_download}, user={user_id}"
            )
            celery_task = await asyncio.to_thread(download_unified_task.delay,
                platform_id=platform_id,
                user_id=user_id,
                url=url,
                download_video=dl_video,
                download_cover=dl_cover,
                media_type=media_type,
                video_title=video_title[:50] if video_title else "undefined",
                resource_id=resource_id,
                _unified_task_id=unified_task_id,
            )
            task_id = celery_task.id
            # Write celery_task_id back to pre-created unified_task
            if unified_task_id:
                try:
                    await orchestrator._atomic_update(unified_task_id, {"celery_task_id": task_id})
                except Exception:
                    pass
        except Exception as celery_err:
            logger.warning(f"[Download/Init] Celery unavailable: {celery_err}")
            if background_tasks:
                from app.services.downloader import DownloaderService
                if dl_video:
                    if is_image_type:
                        background_tasks.add_task(DownloaderService.download_images_by_platform_id, platform_id, user_id=user_id)
                    else:
                        background_tasks.add_task(DownloaderService.download_video_by_platform_id, platform_id, user_id=user_id)
                if dl_cover:
                    background_tasks.add_task(DownloaderService.download_cover_by_platform_id, platform_id, user_id=user_id)
                task_id = "background"

    return {
        "task_id": task_id,
        "unified_task_id": unified_task_id,
        "types_submitted": types_to_download,
        "types_skipped": types_skipped,
        "types_subscribed": types_subscribed,
    }


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

    - **url**: Video link (supports share links)
    - **video_bool**: Whether to download video file
    - **use_celery**: Whether to use Celery async tasks (default False)

    Authentication: Bearer Token or API Key (requires `videos:fetch` scope)
    """
    try:
        # Extract valid URL from share text
        try:
            valid_urls = Utils.extract_valid_url(request.url)
            url = valid_urls[0]  # Take the first valid URL
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Cannot extract a valid link from input"
            )

        logger.info(f"[Fetch/Parse] User {auth.user_id} parsing URL, url={url}")

        # === Points check (skip if video already parsed) ===
        points_service = PointsService()
        _team_id = await _resolve_team_id(auth.user_id, raw_request)
        _points_cost = 0
        if _team_id:
            await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)

            # Check if this URL was already parsed (avoid double-charging)
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
        # === End points check ===

        # Detect platform and handler type
        platform, handler_type = URLRouter.detect_platform(url)
        logger.info(f"[URLRouter] Platform: {platform}, Handler: {handler_type}")

        # ==========================================
        # Unified path: all platforms use yt-dlp (with Douyin fallback)
        # ==========================================
        return await _handle_ytdlp_fetch(
            url=url,
            platform=platform,
            request=request,
            background_tasks=background_tasks,
            auth=auth,
            tags=request.tags,
            tag_ids=request.tag_ids,
        )

    except HTTPException as he:
        # Refund points on failure (skip 402 which means insufficient balance)
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
        # Log failure
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
        # Refund points on failure
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
        # Log failure
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

    Unlike POST /fetch (first-time parse), this endpoint does NOT re-parse
    the original URL. It uses existing metadata to trigger downloads for
    the requested types.

    - **platform_id**: The media's platform identifier
    - **types**: List of types to fetch: "video", "cover", "image"

    Authentication: Bearer Token or API Key
    """
    try:
        logger.info(
            f"[Download/Init] User {auth.user_id} requesting {request.types} "
            f"for {platform_id}"
        )

        # 1) Load existing parsed_media
        repo = MediaRepository()
        media = await repo.get_by_platform_id(platform_id)
        if not media:
            raise HTTPException(status_code=404, detail="Media not found. Use POST /videos/fetch first.")

        media_id = media.get("id")
        media_type = media.get("media_type", 0)
        video_title = media.get("title", platform_id)

        # 2) Download does not consume points (already charged at parse time)

        # 3) Ensure user resource record exists
        from app.repositories.resources_repository import ResourcesRepository

        resources_repo = ResourcesRepository()
        user_resource = await resources_repo.get_resource_by_media_id_and_creator(
            media_id, auth.user_id
        )
        resource_id = user_resource.get("id") if user_resource else None

        if not resource_id:
            # Create a minimal resource record
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
            # Update status for newly requested types (skipped -> pending)
            status_updates = {}
            for t in request.types:
                status_field = f"{t}_download_status"
                if user_resource.get(status_field) == "skipped":
                    status_updates[status_field] = "pending"
            if status_updates:
                await resources_repo.update_download_status(resource_id, status_updates)

        # 4) Detect platform: pass URL for yt-dlp platforms so Celery uses the right strategy
        original_url = media.get("original_url")
        dispatch_url = None
        if original_url:
            _, handler_type = URLRouter.detect_platform(original_url)
            if handler_type == "ytdlp":
                dispatch_url = original_url

        # 5) Dedup + dispatch
        dispatch_result = await _dedup_and_dispatch(
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

        # 6) Log action
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
            "download_task_id": dispatch_result["task_id"],
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

    Use this when audio extraction failed or audio file is missing but
    the video file exists on disk.

    - **platform_id**: The media's platform identifier

    Authentication: Bearer Token or API Key
    """
    try:
        repo = MediaRepository()
        media = await repo.get_by_platform_id(platform_id)
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")

        # Check that a video file exists
        download_path = media.get("download_path")
        if not download_path:
            raise HTTPException(
                status_code=400,
                detail="No video file found. Download the video first.",
            )

        video_title = media.get("title", platform_id)[:30]

        # Create unified_tasks record for Task Center visibility
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

        # Dispatch extraction in background
        # Note: _extract_audio_from_video is sync (uses asyncio.run internally
        # for DB calls), so we must run it in a thread to avoid
        # "cannot call asyncio.run() from a running event loop".
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


@router.post("/fetch/batch", tags=TAGS_FETCH)
async def fetch_videos_batch(
    request: BatchFetchRequest, background_tasks: BackgroundTasks, auth: AuthDep,
    raw_request: Request,
):
    """
    Batch fetch videos

    Parse multiple video links at once, suitable for batch collection.

    - **urls**: List of video links
    - **video_bool**: Whether to download video files
    - **use_celery**: Whether to use Celery async tasks (default False)

    Authentication: Bearer Token or API Key (requires `videos:fetch:batch` scope)
    """
    # === Points check ===
    points_service = PointsService()
    _team_id = await _resolve_team_id(auth.user_id, raw_request)
    _batch_points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="video_parse_batch",
            count=len(request.urls),
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _batch_points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    # If using Celery async tasks
    if request.use_celery:
        from app.tasks.parse_tasks import parse_batch_links_task

        task = await asyncio.to_thread(parse_batch_links_task.delay,
            urls=request.urls,
            user_id=auth.user_id,
            video_bool=request.video_bool,
            cover_bool=request.cover_bool,
        )

        # Log action
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch_batch",
            message=f"Submitted batch Celery task: {len(request.urls)} links",
            status="pending",
            details={"url_count": len(request.urls)},
        )

        return {
            "success": True,
            "message": "Batch task submitted to Celery queue",
            "task_id": task.id,
            "total": len(request.urls),
            "use_celery": True,
        }

    # Default: use BackgroundTasks (original logic)
    results = []
    errors = []

    # Read user's parse mode setting
    user_parse_mode = "lighthttp"  # Default
    try:
        settings_repo = UserSettingsRepository()
        user_settings = await settings_repo.get_by_user_id(auth.user_id)
        if user_settings and user_settings.get("settings_json"):
            user_parse_mode = user_settings["settings_json"].get(
                "parse_mode", "lighthttp"
            )
        logger.info(f"[Batch Parse] User {auth.user_id} parse mode: {user_parse_mode}")
    except Exception as e:
        logger.warning(f"Failed to read user parse mode, using default: {e}")

    for raw_url in request.urls:
        try:
            # Extract valid URL from share text
            try:
                valid_urls = Utils.extract_valid_url(raw_url)
                url = valid_urls[0]
            except ValueError:
                errors.append({"url": raw_url, "error": "Cannot extract valid link"})
                continue

            # Choose parser based on user setting
            aweme_detail = None

            if user_parse_mode == "drissionpage":
                # User selected DrissionPage, use browser parsing directly
                try:
                    aweme_detail = await DouyinAnalysis.fetch_one_video(url)
                except Exception as e:
                    logger.warning(f"[Batch Parse] Browser parsing failed: {e}")
            else:
                # Default mode: try LightHTTP first, then fallback
                try:
                    aweme_detail = await LightweightParser.parse(url)
                except Exception as e:
                    logger.warning(f"[Batch Parse] Lightweight parsing failed: {e}")

                # Fallback to browser automation
                if not aweme_detail:
                    try:
                        aweme_detail = await DouyinAnalysis.fetch_one_video(url)
                    except Exception as e:
                        logger.warning(f"[Batch Parse] Browser parsing failed: {e}")

            if aweme_detail:
                parsed_data = await DouyinParser.parse_aweme_detail(
                    aweme_detail=aweme_detail,
                    valid_url=url,
                    download_video=request.video_bool,
                    download_music=False,
                    download_cover=request.cover_bool,
                )

                if parsed_data:
                    platform_id = parsed_data.get("platform_id")
                    # Add user ID
                    parsed_data["user_id"] = auth.user_id
                    background_tasks.add_task(
                        MediaService.process_video, platform_id, parsed_data
                    )

                    # Attach tags after resource is created
                    if request.tag_ids or request.tags:
                        async def _attach_tags_after_save(
                            pid: str,
                            t_ids: list[str] | None,
                            t_names: list[str] | None,
                            uid: str,
                        ):
                            """Wait for resource to be created, then attach tags."""
                            import asyncio
                            from app.repositories.resources_repository import ResourcesRepository
                            res_repo = ResourcesRepository()
                            for _ in range(10):
                                resource = await res_repo.get_resource_by_platform_id(pid)
                                if resource:
                                    rid = str(resource["id"])
                                    if t_ids:
                                        tags_repo = TagsRepository()
                                        await tags_repo.bulk_add_tags_to_resource(
                                            rid, t_ids, source="manual"
                                        )
                                    if t_names:
                                        await _resolve_and_attach_tags(rid, t_names, uid)
                                    logger.info(f"Tags attached to resource {rid} for {pid}")
                                    return
                                await asyncio.sleep(1)
                            logger.warning(f"Timeout attaching tags for {pid}")

                        background_tasks.add_task(
                            _attach_tags_after_save,
                            platform_id,
                            request.tag_ids,
                            request.tags,
                            auth.user_id,
                        )

                    # Handle datetime objects to string
                    published_at = parsed_data.get("published_at")
                    if published_at and hasattr(published_at, "isoformat"):
                        published_at = published_at.isoformat()

                    # Return complete data for frontend immediate display
                    results.append(
                        {
                            "url": url,
                            "platform_id": platform_id,
                            "status": "submitted",
                            "data": {
                                "platform_id": platform_id,
                                "title": parsed_data.get("title"),
                                "description": parsed_data.get("description"),
                                "author": parsed_data.get("author"),
                                "media_type": parsed_data.get("media_type"),
                                "video_download_urls": parsed_data.get(
                                    "video_download_urls", []
                                ),
                                "cover_urls": parsed_data.get("cover_urls", []),
                                "like_count": parsed_data.get("like_count", 0),
                                "comment_count": parsed_data.get("comment_count", 0),
                                "share_count": parsed_data.get("share_count", 0),
                                "favorite_count": parsed_data.get("favorite_count", 0),
                                "duration": parsed_data.get("duration", "0"),
                                "published_at": published_at,
                                "image_urls": parsed_data.get("image_urls", []),
                                "sec_uid": parsed_data.get("sec_uid"),
                                "unique_id": parsed_data.get("unique_id"),
                                "valid_url": url,
                                "user_id": auth.user_id,
                            },
                        }
                    )
                else:
                    errors.append({"url": url, "error": "Parse failed"})
            else:
                errors.append({"url": url, "error": "Cannot fetch video info"})

        except Exception as e:
            errors.append({"url": url, "error": str(e)})

    # Refund points for failed URLs in the batch
    if errors and _batch_points_cost > 0 and _team_id and len(request.urls) > 0:
        per_url_cost = _batch_points_cost // len(request.urls)
        refund_amount = per_url_cost * len(errors)
        if refund_amount > 0:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=refund_amount,
                    reference_type="video_parse_batch",
                    reason=f"Partial batch refund: {len(errors)}/{len(request.urls)} URLs failed",
                )
                logger.info(
                    f"Refunded {refund_amount} points for {len(errors)} failed batch URLs"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund batch points: {refund_err}")

    return {
        "success": True,
        "total": len(request.urls),
        "submitted": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


@router.post("/cleanup-stale-downloads", tags=TAGS_VIDEOS)
async def cleanup_stale_downloads(
    auth: AuthDep,
    timeout_minutes: int = Query(30, ge=5, le=120),
):
    """
    Mark downloads stuck in 'downloading' state as 'failed'.

    Any download that has been in 'downloading' status for longer than
    timeout_minutes will be marked as 'failed' so users can retry.

    - **timeout_minutes**: Minutes before a download is considered stale (default 30)
    """
    try:
        repo = MediaRepository()
        count = await repo.mark_stale_downloads_failed(timeout_minutes)
        return {"success": True, "cleaned": count}
    except Exception as e:
        logger.error(f"Stale download cleanup failed: {e}")
        raise HTTPException(status_code=500, detail="Cleanup failed")


@router.get("/videos", tags=TAGS_VIDEOS)
async def list_videos(
    auth: AuthDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    order_by: str = Query("created_at"),
    ascending: bool = Query(False),
):
    """
    Get video list

    Query collected and stored video data.

    - **skip**: Number of records to skip (pagination)
    - **limit**: Number of records to return (1-100)
    - **order_by**: Sort field
    - **ascending**: Whether ascending

    Authentication: Bearer Token or API Key (requires `videos:videos:read` scope)
    """
    try:
        repo = MediaRepository()
        videos = await repo.get_user_media_list(
            user_id=auth.user_id,
            skip=skip,
            limit=limit,
            order_by=order_by,
            ascending=ascending,
        )
        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"Failed to get video list: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video list")


@router.get("/videos/{platform_id}", tags=TAGS_VIDEOS)
async def get_video(platform_id: str, auth: AuthDep):
    """
    Get video details

    Get full info of a single video by platform_id.

    - **platform_id**: Video unique identifier

    Authentication: Bearer Token or API Key (requires `videos:videos:read` scope)
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # Merge user's resource download statuses
        media_id = video.get("id")
        if media_id:
            from app.repositories.resources_repository import ResourcesRepository
            res_repo = ResourcesRepository()
            user_resource = await res_repo.get_resource_by_media_id_and_creator(
                media_id, auth.user_id
            )
            if user_resource:
                video["resource_id"] = user_resource["id"]
                for field in ("video_download_status", "music_download_status",
                              "cover_download_status", "image_download_status"):
                    user_status = user_resource.get(field)
                    if user_status is not None:
                        video[field] = user_status

        return {"success": True, "video": video}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get video details: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video details")


@router.delete("/videos/{platform_id}", tags=TAGS_VIDEOS)
async def delete_video(
    platform_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
    delete_files: bool = Query(
        False, description="Whether to also delete locally downloaded files"
    ),
):
    """
    Delete video record

    Remove video record from database, optionally delete locally downloaded files.

    - **platform_id**: Video unique identifier
    - **delete_files**: Whether to delete local files (default False)

    Authentication: Bearer Token or API Key (requires `videos:videos:write` scope)
    """
    try:
        repo = MediaRepository()

        # Get video info for logging and file deletion
        video = await repo.get_by_platform_id(platform_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_title = video.get("title", platform_id)[:30] if video else platform_id
        files_deleted = []

        # Delete local files
        if delete_files:
            import shutil

            # Delete video/image files
            download_path = video.get("download_path")
            if download_path:
                path = Path(download_path)
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                        files_deleted.append(f"directory: {path.name}")
                    else:
                        path.unlink()
                        files_deleted.append(f"file: {path.name}")

            # Delete cover file
            cover_path = video.get("cover_download_path")
            if cover_path:
                path = Path(cover_path)
                if path.exists():
                    path.unlink()
                    files_deleted.append(f"cover: {path.name}")

        # Delete database record
        result = await repo.delete(platform_id)

        if not result:
            raise HTTPException(
                status_code=404, detail="Failed to delete database record"
            )

        # Build log message
        log_message = f"Deleted video: {video_title}..."
        if files_deleted:
            log_message += f" (deleted {len(files_deleted)} local files)"

        # Log deletion
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="delete",
            message=log_message,
            status="success",
            aweme_id=platform_id,
            details={"files_deleted": files_deleted} if files_deleted else None,
        )

        return {
            "success": True,
            "message": "Video deleted",
            "files_deleted": files_deleted,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete video: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete video")


@router.post("/videos/search", tags=TAGS_VIDEOS)
async def search_videos(
    request: MediaSearchRequest,
    auth: AuthDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Search videos

    Multi-criteria search of stored videos.

    - **keyword**: Keyword (title/description)
    - **author**: Author name
    - **status**: Download status
    - **media_type**: Media type
    - **category**: Category tag
    - **start_date/end_date**: Time range

    Authentication: Bearer Token or API Key (requires `videos:search` scope)
    """
    try:
        repo = MediaRepository()

        status = None
        if request.status:
            try:
                status = DownloadStatus(request.status)
            except ValueError:
                pass

        videos = await repo.search(
            user_id=auth.user_id,
            keyword=request.keyword,
            author=request.author,
            status=status,
            media_type=request.media_type,
            category=request.category,
            start_date=request.start_date,
            end_date=request.end_date,
            skip=skip,
            limit=limit,
        )

        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"Failed to search videos: {e}")
        raise HTTPException(status_code=500, detail="Failed to search videos")


@router.get("/statistics", tags=TAGS_STATS)
async def get_statistics(auth: AuthDep):
    """
    Get statistics

    Returns video count, download status distribution, type distribution, etc.

    Authentication: Bearer Token or API Key (requires `videos:statistics` scope)
    """
    try:
        repo = MediaRepository()
        stats = await repo.get_statistics(user_id=auth.user_id)
        return {"success": True, "statistics": stats}
    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")


@router.get("/pending", tags=TAGS_DOWNLOAD)
async def get_pending_downloads(auth: AuthDep, limit: int = Query(100, ge=1, le=500)):
    """
    Get pending downloads list

    Returns list of videos waiting to be downloaded.

    - **limit**: Number of records to return (max 500)

    Authentication: Bearer Token or API Key (requires `videos:videos:read` scope)
    """
    try:
        repo = MediaRepository()
        videos = await repo.get_pending_downloads(
            user_id=auth.user_id, status=DownloadStatus.PENDING, limit=limit
        )
        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"Failed to get pending downloads list: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get pending downloads list"
        )


class RetryDownloadRequest(BaseModel):
    """Retry download request — select which media to re-download."""

    video_bool: bool = True
    cover_bool: bool = False


@router.post("/retry/{platform_id}", tags=TAGS_DOWNLOAD)
async def retry_download(
    platform_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
    request: RetryDownloadRequest = RetryDownloadRequest(),
):
    """
    Retry download

    Re-trigger download for a video. Supports selective media types.

    - **platform_id**: Video unique identifier
    - **video_bool**: Re-download video (default True)
    - **cover_bool**: Re-download cover (default False)

    Authentication: Bearer Token or API Key (requires `videos:retry` scope)
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_title = video.get("title", platform_id)[:30]
        media_type = video.get("media_type", 0)
        media_id = video.get("id")

        # Reset download status on global parsed_media
        status_updates: dict = {"error_message": None}
        if request.video_bool:
            status_updates["video_download_status"] = DownloadStatus.PENDING.value
        if request.cover_bool:
            status_updates["cover_download_status"] = DownloadStatus.PENDING.value

        await repo.update(platform_id, status_updates)

        # Look up user's resource record and reset its statuses too
        resource_id = None
        if media_id:
            from app.repositories.resources_repository import ResourcesRepository
            res_repo = ResourcesRepository()
            user_resource = await res_repo.get_resource_by_media_id_and_creator(
                media_id, auth.user_id
            )
            if user_resource:
                resource_id = user_resource.get("id")
                res_status_updates = {}
                if request.video_bool:
                    res_status_updates["video_download_status"] = "pending"
                if request.cover_bool:
                    res_status_updates["cover_download_status"] = "pending"
                if res_status_updates:
                    await res_repo.update_download_status(resource_id, res_status_updates)

        # Dispatch via shared helper
        dispatch_result = await _dedup_and_dispatch(
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=resource_id,
            media_type=int(media_type) if str(media_type).isdigit() else 0,
            video_title=video_title,
            download_video=request.video_bool,
            download_music=False,
            download_cover=request.cover_bool,
            background_tasks=background_tasks,
        )
        download_task_id = dispatch_result["task_id"]

        # Log retry action
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="retry",
            message=f"Retry download: {video_title}...",
            status="pending",
            aweme_id=platform_id,
        )

        return {
            "success": True,
            "message": "Download task resubmitted",
            "task_id": download_task_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retry download: {e}")
        raise HTTPException(status_code=500, detail="Failed to retry download")


@router.get("/download/{platform_id}", tags=TAGS_DOWNLOAD)
async def download_video_file(platform_id: str, auth: AuthDep):
    """
    Download video file

    Return video file for browser download (sets Content-Disposition: attachment).

    - **platform_id**: Video unique identifier

    Authentication: Bearer Token or API Key (requires `videos:videos:read` scope)
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        download_path = video.get("download_path")
        if not download_path:
            raise HTTPException(status_code=404, detail="Video file path not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")
        file_path = Path(base_path) / download_path
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Video file not found")

        # Generate download filename
        video_title = video.get("title", platform_id)
        # Clean illegal characters from filename
        safe_title = "".join(
            c for c in video_title if c.isalnum() or c in (" ", "-", "_", ".")
        ).strip()
        if not safe_title:
            safe_title = platform_id
        filename = f"{safe_title}.mp4"

        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="video/mp4",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download video file: {e}")
        raise HTTPException(status_code=500, detail="Failed to download video file")


@router.get("/download/{platform_id}/cover", tags=TAGS_DOWNLOAD)
async def download_cover_file(platform_id: str, auth: AuthDep):
    """
    Download cover file

    Return cover image for browser download.

    - **platform_id**: Video unique identifier

    Authentication: Bearer Token or API Key (requires `videos:videos:read` scope)
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        cover_path = video.get("cover_download_path")
        if not cover_path:
            raise HTTPException(status_code=404, detail="Cover file path not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")
        file_path = Path(base_path) / cover_path
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Cover file not found")

        # Generate download filename
        video_title = video.get("title", platform_id)
        safe_title = "".join(
            c for c in video_title if c.isalnum() or c in (" ", "-", "_", ".")
        ).strip()
        if not safe_title:
            safe_title = platform_id
        filename = f"{safe_title}_cover.jpg"

        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="image/jpeg",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download cover file: {e}")
        raise HTTPException(status_code=500, detail="Failed to download cover file")


@router.get("/download/{platform_id}/music", tags=TAGS_DOWNLOAD)
async def download_music_file(platform_id: str, auth: AuthDep):
    """
    Download music/audio file

    Return audio file for browser download.

    - **platform_id**: Video unique identifier

    Authentication: Bearer Token or API Key (requires `videos:videos:read` scope)
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        music_status = video.get("music_download_status", "").lower()
        if music_status not in ("completed", "skipped"):
            raise HTTPException(status_code=404, detail="Music file has not been downloaded")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")

        # Prefer stored music_download_path (like cover_download_path)
        music_download_path = video.get("music_download_path", "")
        audio_file = None

        if music_download_path:
            candidate = Path(base_path) / music_download_path
            if candidate.exists():
                audio_file = candidate

        # Fallback: search by naming patterns in storage directory
        if not audio_file:
            storage_dir = None
            download_path = video.get("download_path", "")
            if download_path:
                storage_dir = Path(base_path) / Path(download_path).parent
            else:
                for pattern in [
                    f"global/resources/web/*/{platform_id}",
                    f"*/{platform_id}",
                ]:
                    matches = list(Path(base_path).glob(pattern))
                    if matches:
                        storage_dir = matches[0]
                        break

            if storage_dir and storage_dir.exists():
                for name in [f"{platform_id}_audio", "music", f"{platform_id}_music", "audio"]:
                    for ext in ["mp3", "m4a", "opus", "ogg", "wav", "aac"]:
                        candidate = storage_dir / f"{name}.{ext}"
                        if candidate.exists():
                            audio_file = candidate
                            break
                    if audio_file:
                        break

        if not audio_file:
            raise HTTPException(status_code=404, detail="Music file not found on disk")

        video_title = video.get("title", platform_id)
        safe_title = "".join(
            c for c in video_title if c.isalnum() or c in (" ", "-", "_", ".")
        ).strip()
        if not safe_title:
            safe_title = platform_id
        suffix = audio_file.suffix or ".mp3"
        filename = f"{safe_title}_audio{suffix}"

        content_type = {
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".opus": "audio/opus",
            ".ogg": "audio/ogg",
            ".wav": "audio/wav",
            ".aac": "audio/aac",
        }.get(suffix, "audio/mpeg")

        return FileResponse(
            path=str(audio_file),
            filename=filename,
            media_type=content_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download music file: {e}")
        raise HTTPException(status_code=500, detail="Failed to download music file")


@router.get("/download/{platform_id}/slides", tags=TAGS_DOWNLOAD)
async def list_slides(platform_id: str, auth: AuthDep):
    """
    List slide files for a carousel/image-text media item.

    Returns an ordered list of files in the slides/ subfolder.

    - **platform_id**: Media unique identifier

    Authentication: Bearer Token or API Key
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Media not found")

        download_path = video.get("download_path")
        if not download_path:
            raise HTTPException(status_code=404, detail="Download path not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")

        # Check slides/ subfolder first (new format), fallback to root folder (old format)
        slides_dir = Path(base_path) / download_path / "slides"
        if not slides_dir.exists() or not slides_dir.is_dir():
            # Fallback: old downloads stored images in root folder
            slides_dir = Path(base_path) / download_path
            if not slides_dir.exists() or not slides_dir.is_dir():
                raise HTTPException(status_code=404, detail="Slides folder not found")

        # List and sort slide files
        slides = []
        for f in sorted(slides_dir.iterdir()):
            if not f.is_file():
                continue
            suffix = f.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                slide_type = "image"
                media_type = f"image/{suffix.lstrip('.')}"
            elif suffix in (".mp4", ".mov", ".webm"):
                slide_type = "video"
                media_type = f"video/{suffix.lstrip('.')}"
            else:
                continue

            slides.append({
                "name": f.name,
                "type": slide_type,
                "media_type": media_type,
                "url": f"/api/v1/videos/download/{platform_id}/slides/{f.name}",
            })

        return {"slides": slides, "count": len(slides)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list slides for {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list slides")


@router.get("/download/{platform_id}/slides/{filename}", tags=TAGS_DOWNLOAD)
async def serve_slide_file(platform_id: str, filename: str, auth: OptionalAuthDep = None, token: str = None):
    """
    Serve a single slide file (image or video clip).

    - **platform_id**: Media unique identifier
    - **filename**: Slide filename (e.g. 001.jpg, 002.mp4)

    Authentication: Bearer Token or API Key
    """
    import mimetypes as _mt
    from app.api.media_auth import validate_media_cookie

    # Auth: Bearer token OR ?token= query param
    if not auth and token:
        user_id = validate_media_cookie(token)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    elif not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Media not found")

        download_path = video.get("download_path")
        if not download_path:
            raise HTTPException(status_code=404, detail="Download path not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")

        # Check slides/ subfolder first, fallback to root folder (old format)
        file_path = Path(base_path) / download_path / "slides" / filename
        if not file_path.exists():
            file_path = Path(base_path) / download_path / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Slide file not found")

        content_type = _mt.guess_type(str(file_path))[0] or "application/octet-stream"

        return FileResponse(
            path=str(file_path),
            media_type=content_type,
            content_disposition_type="inline",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve slide {filename} for {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve slide file")


@router.get("/download/{platform_id}/audio", tags=TAGS_DOWNLOAD)
async def serve_audio_file(platform_id: str, auth: OptionalAuthDep = None, token: str = None):
    """
    Serve the standalone background audio file for carousel content.

    - **platform_id**: Media unique identifier

    Authentication: Bearer Token, API Key, or ?token= query param
    """
    from app.api.media_auth import validate_media_cookie

    if not auth and token:
        user_id = validate_media_cookie(token)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    elif not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Media not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")

        # Try music_download_path first, then fallback to download_path/audio.mp3
        audio_file = None
        music_path = video.get("music_download_path")
        if music_path:
            candidate = Path(base_path) / music_path
            if candidate.exists():
                audio_file = candidate

        if not audio_file:
            download_path = video.get("download_path")
            if download_path:
                candidate = Path(base_path) / download_path / "audio.mp3"
                if candidate.exists():
                    audio_file = candidate

        if not audio_file:
            raise HTTPException(status_code=404, detail="Audio file not found")

        return FileResponse(
            path=str(audio_file),
            media_type="audio/mpeg",
            content_disposition_type="inline",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve audio for {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve audio file")


@router.get("/logs", tags=TAGS_LOGS)
async def get_user_logs(
    auth: AuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    level: Optional[str] = Query(None, description="Filter by status level"),
    date_range: Optional[str] = Query(None, description="Preset date range: 24h, 7days, 30days, 90days"),
    start_date: Optional[str] = Query(None, description="Custom start date (ISO)"),
    end_date: Optional[str] = Query(None, description="Custom end date (ISO)"),
    search: Optional[str] = Query(None, description="Search in message"),
):
    """
    Get user action logs (paginated)

    Authentication: Bearer Token or API Key
    """
    try:
        repo = UserLogsRepository()
        result = await repo.get_paginated(
            user_id=auth.user_id,
            page=page,
            page_size=page_size,
            level=level,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"Failed to get user logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user logs")


async def _douyin_parse_fallback(url: str, user_id: str) -> tuple[dict, str, str]:
    """Douyin parse fallback: LightHTTP → DrissionPage.

    Called when yt-dlp fails for a Douyin URL.

    Returns:
        tuple of (parsed_data, parse_method, parse_method_name)

    Raises:
        HTTPException(404) if all parsers fail
    """
    aweme_detail = None
    parse_method = "unknown"
    parse_method_name = "Unknown"
    fallback_reason = None

    # Read user's parse mode setting
    user_parse_mode = "lighthttp"  # Default
    try:
        settings_repo = UserSettingsRepository()
        user_settings = await settings_repo.get_by_user_id(user_id)
        if user_settings and user_settings.get("settings_json"):
            user_parse_mode = user_settings["settings_json"].get(
                "parse_mode", "lighthttp"
            )
        logger.info(f"[Douyin Fallback] User {user_id} parse_mode: {user_parse_mode}")
    except Exception as e:
        logger.warning(f"Failed to read user parse mode, using default: {e}")

    if user_parse_mode == "drissionpage":
        # User selected DrissionPage — use browser parsing directly
        try:
            logger.info(f"[BrowserAuto] User selected browser parsing: {url}")
            aweme_detail = await DouyinAnalysis.fetch_one_video(url)
            if aweme_detail:
                parse_method = "browser_auto"
                parse_method_name = "BrowserAuto"
                logger.success("[BrowserAuto] Parse successful")
        except Exception as e:
            logger.error(f"[BrowserAuto] Parse failed: {e}")
            fallback_reason = f"BrowserAuto error: {str(e)[:50]}"
    else:
        # Default (lighthttp): LightHTTP first, then DrissionPage fallback
        try:
            logger.info(f"[LightHTTP] Attempting parse: {url}")
            aweme_detail = await LightweightParser.parse(url)
            if aweme_detail:
                parse_method = "light_http"
                parse_method_name = "LightHTTP"
                logger.success("[LightHTTP] Parse successful")
            else:
                fallback_reason = "LightHTTP returned empty result"
        except Exception as e:
            fallback_reason = f"LightHTTP error: {str(e)[:50]}"
            logger.warning(f"[LightHTTP] Parse failed: {e}")

        # DrissionPage fallback
        if not aweme_detail:
            try:
                logger.info(f"[BrowserAuto] Falling back to browser parsing: {url}")
                aweme_detail = await DouyinAnalysis.fetch_one_video(url)
                if aweme_detail:
                    parse_method = "browser_auto"
                    parse_method_name = "BrowserAuto"
                    logger.success("[BrowserAuto] Parse successful")
            except Exception as e:
                logger.error(f"[BrowserAuto] Parse failed: {e}")

    if not aweme_detail:
        raise HTTPException(
            status_code=404,
            detail="Cannot fetch video info (yt-dlp, LightHTTP and BrowserAuto all failed)",
        )

    # Convert raw aweme_detail to our parsed_data format
    parsed_data = await DouyinParser.parse_aweme_detail(
        aweme_detail=aweme_detail,
        valid_url=url,
        download_video=True,
        download_music=False,
        download_cover=True,
    )
    if not parsed_data:
        raise HTTPException(status_code=500, detail="Douyin video parsing failed")

    return parsed_data, parse_method, parse_method_name


async def _handle_ytdlp_fetch(
    url: str,
    platform: str,
    request: MediaFetchRequest,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
    tags: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
) -> dict:
    """
    Unified video fetch handler for ALL platforms via yt-dlp.

    Async flow: dispatches parse_media_task to Celery and returns immediately.
    The Celery task handles: yt-dlp metadata fetch → save DB → dispatch download.
    Progress is visible in Task Center via unified_tasks.
    """
    from app.services.unified_task_manager import get_task_manager
    from app.tasks.parse_tasks import parse_media_task

    mgr = get_task_manager()

    # Dedup check for parse (URL as dedup identifier)
    dedup_key = None
    try:
        result = await mgr.acquire_or_subscribe(
            task_type="parse",
            dedup_identifier=url,
            user_id=auth.user_id,
            resource_id="",
        )
        if result["action"] in ("subscribed", "completed"):
            return {
                "success": True,
                "async": True,
                "message": f"Parse already {result['action']}",
                "dedup_action": result["action"],
            }
        dedup_key = result.get("dedup_key")
    except Exception as e:
        logger.warning(f"[Parse/Dedup] check failed, proceeding: {e}")

    # Pre-create parse unified_task
    unified_task_id = None
    try:
        unified_task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="parse",
            title=f"Parsing {url[:40]}...",
            subtitle="Initializing...",
            dedup_key=dedup_key,
        )
    except Exception as e:
        logger.warning(f"[Parse] Pre-create unified_task failed: {e}")

    # Dispatch Celery parse task
    celery_task = await asyncio.to_thread(
        parse_media_task.delay,
        url=url,
        platform=platform,
        user_id=auth.user_id,
        video_bool=request.video_bool,
        cover_bool=True,
        tags=tags,
        tag_ids=tag_ids,
        _unified_task_id=unified_task_id,
        _dedup_key=dedup_key,
    )

    # Write celery_task_id back to pre-created unified_task
    if unified_task_id:
        try:
            await mgr._atomic_update(unified_task_id, {"celery_task_id": celery_task.id})
        except Exception:
            pass

    # Log action
    background_tasks.add_task(
        log_user_action,
        user_id=auth.user_id,
        action="fetch",
        message=f"Parse submitted: {url[:40]}...",
        status="success",
        details={"platform": platform, "async": True},
    )

    return {
        "success": True,
        "async": True,
        "message": "Parse task submitted",
        "parse_task_id": celery_task.id,
        "unified_task_id": unified_task_id,
    }


# ============================================
# Legacy redirect: /douyin/* -> /videos/*
# ============================================
from fastapi import Request  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402

legacy_router = APIRouter(prefix="/douyin")


@legacy_router.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False
)
async def legacy_douyin_redirect(path: str, request: Request):
    """Redirect legacy /douyin/ routes to /media/"""
    new_url = str(request.url).replace("/douyin/", "/videos/", 1)
    return RedirectResponse(url=new_url, status_code=308)
