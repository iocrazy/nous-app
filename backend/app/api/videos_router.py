# backend/app/api/videos_router.py

"""
Videos Router

Video processing API endpoints based on Supabase.
Requires authentication (JWT or API Key).
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.repositories.user_logs_repository import UserLogsRepository, log_user_action
from app.repositories.user_settings_repository import UserSettingsRepository
from app.repositories.video_repository import VideoRepository
from app.services.douyin_analysis import DouyinAnalysis
from app.services.douyin_parser import DouyinParser
from app.services.lightweight_parser import LightweightParser
from app.services.url_router import URLRouter
from app.services.points_service import PointsService
from app.services.video_service import VideoService
from app.services.ytdlp_service import YtdlpService

router = APIRouter(prefix="/videos")

# API group tags
TAGS_FETCH = ["Video Fetch"]  # Fetch and parse videos
TAGS_VIDEOS = ["Video Management"]  # CRUD for stored data
TAGS_STATS = ["Statistics"]  # Statistics and analytics
TAGS_DOWNLOAD = ["Download Management"]  # Download operations
TAGS_LOGS = ["Logs"]  # User action logs


# ============================================
# Request/Response models
# ============================================


class VideoFetchRequest(BaseModel):
    """Video fetch request"""

    url: str
    video_bool: bool = True
    music_bool: bool = False
    cover_bool: bool = True
    use_celery: bool = False  # Whether to use Celery async tasks


class VideoSearchRequest(BaseModel):
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
    music_bool: bool = False
    cover_bool: bool = True
    use_celery: bool = False  # Whether to use Celery async tasks


# ============================================
# Route endpoints
# ============================================


@router.post("/fetch", tags=TAGS_FETCH)
async def fetch_video(
    request: VideoFetchRequest, background_tasks: BackgroundTasks, auth: AuthDep
):
    """
    Fetch a single video

    Parse video info from a link and automatically download.

    - **url**: Video link (supports share links)
    - **video_bool**: Whether to download video file
    - **music_bool**: Whether to download background music
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

        logger.info(f"User {auth.user_id} starting video fetch: {url}")

        # === Points check ===
        points_service = PointsService()
        from app.db.supabase_client import get_async_supabase_admin as _get_admin
        _admin = await _get_admin()
        _tm = await _admin.table("team_members").select("team_id").eq("user_id", auth.user_id).limit(1).execute()
        _team_id = _tm.data[0]["team_id"] if _tm.data else None
        _points_cost = 0
        if _team_id:
            await points_service.ensure_team_quota(_team_id)
            points_result = await points_service.check_and_consume(
                team_id=_team_id,
                user_id=auth.user_id,
                action_type="video_parse",
            )
            if not points_result["success"]:
                raise HTTPException(status_code=402, detail=points_result["reason"])
            _points_cost = points_result.get("points_cost", 0)
        # === End points check ===

        # Detect platform and handler type
        platform, handler_type = URLRouter.detect_platform(url)
        logger.info(f"[URLRouter] Platform: {platform}, Handler: {handler_type}")

        # ==========================================
        # Non-Douyin path: use yt-dlp
        # ==========================================
        if handler_type == "ytdlp":
            return await _handle_ytdlp_fetch(
                url=url,
                platform=platform,
                request=request,
                background_tasks=background_tasks,
                auth=auth,
            )

        # ==========================================
        # Douyin path: existing flow
        # ==========================================

        # If using Celery async tasks
        if request.use_celery:
            from app.tasks.parse_tasks import parse_single_link_task

            task = parse_single_link_task.delay(
                url=url,
                user_id=auth.user_id,
                video_bool=request.video_bool,
                music_bool=request.music_bool,
                cover_bool=request.cover_bool,
            )

            # Log action
            background_tasks.add_task(
                log_user_action,
                user_id=auth.user_id,
                action="fetch",
                message=f"Submitted Celery task: {url[:30]}...",
                status="pending",
            )

            return {
                "success": True,
                "message": "Task submitted to Celery queue",
                "task_id": task.id,
                "url": url,
                "use_celery": True,
            }

        # Default flow: parse metadata + Celery download task
        # Choose parse mode based on user settings
        aweme_detail = None
        parse_method = "unknown"
        parse_method_name = "Unknown"
        fallback_used = False
        fallback_reason = None

        # Read user's parse mode setting
        user_parse_mode = "lighthttp"  # Default
        try:
            settings_repo = UserSettingsRepository()
            user_settings = await settings_repo.get_by_user_id(auth.user_id)
            if user_settings and user_settings.get("settings_json"):
                user_parse_mode = user_settings["settings_json"].get(
                    "parse_mode", "lighthttp"
                )
            logger.info(f"[Parse Mode] User {auth.user_id} setting: {user_parse_mode}")
        except Exception as e:
            logger.warning(f"Failed to read user parse mode, using default: {e}")

        # Choose parser based on user setting
        if user_parse_mode == "drissionpage":
            # User selected DrissionPage, use browser parsing directly
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
            # Default mode (lighthttp): try LightHTTP first, fallback to browser automation
            # Option 1: LightHTTP (lightweight HTTP parsing, no browser required)
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

            # Option 2: BrowserAuto (browser automation, fallback)
            if not aweme_detail:
                fallback_used = True
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
                detail="Cannot fetch video info (both LightHTTP and BrowserAuto failed)",
            )

        logger.info(f"[Parse Complete] Method used: {parse_method_name}")

        # Parse video data (without downloading)
        parsed_data = await DouyinParser.parse_aweme_detail(
            aweme_detail=aweme_detail,
            valid_url=url,
            download_video=request.video_bool,
            download_music=request.music_bool,
            download_cover=request.cover_bool,
        )

        if not parsed_data:
            raise HTTPException(status_code=500, detail="Video parsing failed")

        platform_id = parsed_data.get("platform_id")
        media_type = parsed_data.get("media_type", 0)
        video_title = parsed_data.get("title", "")

        # Add user ID
        parsed_data["user_id"] = auth.user_id

        # Save metadata to database first (must wait for completion, otherwise Celery task can't find data)
        save_result = await VideoService.save_metadata_only(platform_id, parsed_data)
        if not save_result.get("success"):
            logger.error(f"Failed to save metadata: {save_result.get('message')}")
            raise HTTPException(
                status_code=500,
                detail=save_result.get("message", "Failed to save metadata"),
            )

        # Download media files
        download_task_id = None
        need_download = request.video_bool or request.music_bool or request.cover_bool

        if need_download:
            # Try Celery, fallback to FastAPI background tasks if unavailable
            try:
                from app.celery_app import celery_app
                from app.tasks.download_tasks import download_media_task

                # Check if Celery is available - ping() returns empty list if no workers
                workers = celery_app.control.ping(timeout=1)
                if not workers:
                    raise RuntimeError("No Celery workers available")

                download_task = download_media_task.delay(
                    platform_id=platform_id,
                    user_id=auth.user_id,
                    download_video=request.video_bool,
                    download_music=request.music_bool,
                    download_cover=request.cover_bool,
                    media_type=media_type,
                    video_title=video_title[:50] if video_title else "undefined",
                )
                download_task_id = download_task.id
                logger.info(f"Celery download task submitted: {download_task_id}")
            except Exception as celery_err:
                # Celery unavailable, use FastAPI background tasks
                logger.warning(
                    f"Celery unavailable, using FastAPI background tasks: {celery_err}"
                )
                from app.services.downloader import DownloaderService

                # Add download tasks based on type
                if int(media_type) in (0, 4, 61):  # Video types
                    if request.video_bool:
                        background_tasks.add_task(
                            DownloaderService.download_video_by_platform_id,
                            platform_id,
                            user_id=auth.user_id,
                        )
                elif int(media_type) in (2, 68):  # Image types
                    if request.video_bool:
                        background_tasks.add_task(
                            DownloaderService.download_images_by_platform_id,
                            platform_id,
                            user_id=auth.user_id,
                        )

                if request.music_bool:
                    background_tasks.add_task(
                        DownloaderService.download_music_by_platform_id,
                        platform_id=platform_id,
                        user_id=auth.user_id,
                    )

                if request.cover_bool:
                    background_tasks.add_task(
                        DownloaderService.download_cover_by_platform_id,
                        platform_id,
                        user_id=auth.user_id,
                    )
                logger.info(f"FastAPI background download tasks added: {platform_id}")

        # Log action
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"Video parsed successfully: {video_title[:30]}...",
            status="success",
            aweme_id=platform_id,
            details={"platform": "douyin", "parse_method": parse_method},
        )

        # Handle datetime objects to string
        published_at = parsed_data.get("published_at")
        if published_at and hasattr(published_at, "isoformat"):
            published_at = published_at.isoformat()

        # Return complete parsed data for frontend display
        return {
            "success": True,
            "message": "Video processing task submitted",
            "parse_method": parse_method,
            "parse_method_name": parse_method_name,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "id": save_result.get("id"),  # Database ID for tag operations
            "platform_id": platform_id,
            "title": parsed_data.get("title"),
            "author": parsed_data.get("author"),
            "media_type": parsed_data.get("media_type"),
            # Video/cover URLs
            "video_download_urls": parsed_data.get("video_download_urls", []),
            "cover_urls": parsed_data.get("cover_urls", []),
            "image_download_urls": parsed_data.get("image_download_urls", []),
            # Statistics
            "like_count": parsed_data.get("like_count", 0),
            "comment_count": parsed_data.get("comment_count", 0),
            "share_count": parsed_data.get("share_count", 0),
            "favorite_count": parsed_data.get("favorite_count", 0),
            # Video info
            "duration": parsed_data.get("duration", "0"),
            "published_at": published_at,
            "description": parsed_data.get("description"),
            "original_url": parsed_data.get("original_url"),
            "resolution": parsed_data.get("resolution"),
            # Download status
            "video_download_status": parsed_data.get(
                "video_download_status", "PENDING"
            ),
            # Download task ID (for frontend progress polling)
            "download_task_id": download_task_id,
        }

    except HTTPException as he:
        # Refund points on failure (skip 402 which means insufficient balance)
        if _points_cost > 0 and _team_id and getattr(he, 'status_code', 0) != 402:
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
            details={"error": he.detail if hasattr(he, 'detail') else str(he)},
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


@router.post("/fetch/batch", tags=TAGS_FETCH)
async def fetch_videos_batch(
    request: BatchFetchRequest, background_tasks: BackgroundTasks, auth: AuthDep
):
    """
    Batch fetch videos

    Parse multiple video links at once, suitable for batch collection.

    - **urls**: List of video links
    - **video_bool**: Whether to download video files
    - **music_bool**: Whether to download background music
    - **use_celery**: Whether to use Celery async tasks (default False)

    Authentication: Bearer Token or API Key (requires `videos:fetch:batch` scope)
    """
    # === Points check ===
    points_service = PointsService()
    from app.db.supabase_client import get_async_supabase_admin as _get_admin
    _admin = await _get_admin()
    _tm = await _admin.table("team_members").select("team_id").eq("user_id", auth.user_id).limit(1).execute()
    _team_id = _tm.data[0]["team_id"] if _tm.data else None
    _batch_points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id)
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

        task = parse_batch_links_task.delay(
            urls=request.urls,
            user_id=auth.user_id,
            video_bool=request.video_bool,
            music_bool=request.music_bool,
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
                    download_music=request.music_bool,
                    download_cover=request.cover_bool,
                )

                if parsed_data:
                    platform_id = parsed_data.get("platform_id")
                    # Add user ID
                    parsed_data["user_id"] = auth.user_id
                    background_tasks.add_task(
                        VideoService.process_video, platform_id, parsed_data
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
                logger.info(f"Refunded {refund_amount} points for {len(errors)} failed batch URLs")
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
        repo = VideoRepository()
        videos = await repo.get_all(
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
        repo = VideoRepository()
        video = await repo.get_by_platform_id(platform_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

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
        repo = VideoRepository()

        # Get video info for logging and file deletion
        video = await repo.get_by_platform_id(platform_id, user_id=auth.user_id)
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
        result = await repo.delete(platform_id, user_id=auth.user_id)

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
    request: VideoSearchRequest,
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
        repo = VideoRepository()

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
        repo = VideoRepository()
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
        repo = VideoRepository()
        videos = await repo.get_pending_downloads(
            user_id=auth.user_id, status=DownloadStatus.PENDING, limit=limit
        )
        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"Failed to get pending downloads list: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get pending downloads list"
        )


@router.post("/retry/{platform_id}", tags=TAGS_DOWNLOAD)
async def retry_download(
    platform_id: str, background_tasks: BackgroundTasks, auth: AuthDep
):
    """
    Retry download

    Re-trigger download for a failed video.

    - **platform_id**: Video unique identifier

    Authentication: Bearer Token or API Key (requires `videos:retry` scope)
    """
    try:
        repo = VideoRepository()
        video = await repo.get_by_platform_id(platform_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_title = video.get("title", platform_id)[:30]

        # Reset download status
        await repo.update(
            platform_id,
            {
                "video_download_status": DownloadStatus.PENDING.value,
                "error_message": None,
            },
            user_id=auth.user_id,
        )

        # Add background download task (pass user_id for data isolation)
        from app.services.downloader import DownloaderService

        background_tasks.add_task(
            DownloaderService.download_video_by_platform_id,
            platform_id,
            user_id=auth.user_id,
        )

        # Log retry action
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="retry",
            message=f"Retry download: {video_title}...",
            status="pending",
            aweme_id=platform_id,
        )

        return {"success": True, "message": "Download task resubmitted"}
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
        repo = VideoRepository()
        video = await repo.get_by_platform_id(platform_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        download_path = video.get("download_path")
        if not download_path:
            raise HTTPException(status_code=404, detail="Video file path not found")

        file_path = Path(download_path)
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
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
        repo = VideoRepository()
        video = await repo.get_by_platform_id(platform_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        cover_path = video.get("cover_download_path")
        if not cover_path:
            raise HTTPException(status_code=404, detail="Cover file path not found")

        file_path = Path(cover_path)
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
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download cover file: {e}")
        raise HTTPException(status_code=500, detail="Failed to download cover file")


@router.get("/logs", tags=TAGS_LOGS)
async def get_user_logs(
    auth: AuthDep,
    limit: int = Query(20, ge=1, le=100),
    action: Optional[str] = Query(None, description="Filter by action type"),
):
    """
    Get user action logs

    Returns user's recent action log records.

    - **limit**: Number of records (1-100)
    - **action**: Filter specific action type (fetch, download, delete, retry, update)

    Authentication: Bearer Token or API Key
    """
    try:
        repo = UserLogsRepository()
        logs = await repo.get_recent(user_id=auth.user_id, limit=limit, action=action)
        return {"success": True, "count": len(logs), "logs": logs}
    except Exception as e:
        logger.error(f"Failed to get user logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user logs")


async def _handle_ytdlp_fetch(
    url: str,
    platform: str,
    request: VideoFetchRequest,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
) -> dict:
    """
    Handle video fetch for non-Douyin platforms via yt-dlp.

    Fetches metadata, saves to database, and dispatches download tasks.
    """
    # Step 1: Fetch metadata via yt-dlp
    try:
        ytdlp_info = await YtdlpService.fetch_metadata(url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Step 2: Map yt-dlp metadata to our Video schema
    parsed_data = YtdlpService._map_metadata_to_video(ytdlp_info, url)

    # Step 2.5: Enrich Bilibili stats (favorite_count, share_count)
    if platform == "bilibili" and parsed_data.get("external_id"):
        bvid = parsed_data["external_id"]
        extra_stats = await YtdlpService._fetch_bilibili_stats(bvid)
        if extra_stats:
            parsed_data["favorite_count"] = extra_stats.get("favorite", 0)
            parsed_data["share_count"] = extra_stats.get("share", 0)
            logger.info(
                f"[yt-dlp] Bilibili stats enriched: fav={parsed_data['favorite_count']}, share={parsed_data['share_count']}"
            )

    platform_id = parsed_data["platform_id"]
    video_title = parsed_data.get("title", "")

    # Add user-specific fields
    parsed_data["user_id"] = auth.user_id
    parsed_data["need_download_video"] = request.video_bool
    parsed_data["need_download_music"] = request.music_bool
    parsed_data["need_download_cover"] = request.cover_bool

    # Step 3: Save metadata to database
    save_result = await VideoService.save_metadata_only(platform_id, parsed_data)
    if not save_result.get("success"):
        logger.error(f"Failed to save metadata: {save_result.get('message')}")
        raise HTTPException(
            status_code=500,
            detail=save_result.get("message", "Failed to save metadata"),
        )

    # Step 4: Dispatch download tasks
    download_task_id = None
    need_download = request.video_bool or request.music_bool or request.cover_bool

    if need_download:
        # Try Celery first, fallback to FastAPI background tasks
        try:
            from app.celery_app import celery_app
            from app.tasks.download_tasks import download_ytdlp_task

            workers = celery_app.control.ping(timeout=1)
            if not workers:
                raise RuntimeError("No Celery workers available")

            download_task = download_ytdlp_task.delay(
                url=url,
                platform_id=platform_id,
                user_id=auth.user_id,
                download_video=request.video_bool,
                download_music=request.music_bool,
                download_cover=request.cover_bool,
                video_title=video_title[:50] if video_title else "undefined",
            )
            download_task_id = download_task.id
            logger.info(f"[yt-dlp] Celery download task submitted: {download_task_id}")

        except Exception as celery_err:
            logger.warning(
                f"Celery unavailable for yt-dlp download, using background tasks: {celery_err}"
            )

            async def _ytdlp_background_download(
                url: str,
                platform_id: str,
                user_id: str,
                download_video: bool,
                download_music: bool,
                download_cover: bool,
            ):
                """Background task for yt-dlp download"""
                from app.core.enums import DownloadStatus
                from app.core.utils import Utils
                from app.repositories.video_repository import VideoRepository
                from app.services.downloader import DownloaderService

                repo = VideoRepository()
                storage_dir, relative_month = Utils.create_download_folder()

                try:
                    if download_video:
                        result = await YtdlpService.download_video(
                            url, str(storage_dir), platform_id
                        )
                        if result.get("file_path"):
                            file_name = os.path.basename(result["file_path"])
                            relative_path = f"{relative_month}/{file_name}"
                            await repo.mark_video_as_downloaded(
                                platform_id=platform_id,
                                download_path=relative_path,
                                duration=0,
                                storage_size=result.get("file_size", 0),
                            )
                            # Optimize for streaming
                            await DownloaderService.optimize_video_for_streaming(
                                result["file_path"]
                            )

                    if download_music:
                        result = await YtdlpService.download_audio(
                            url, str(storage_dir), platform_id
                        )
                        if result.get("file_path"):
                            await repo.mark_music_as_downloaded(platform_id)

                    if download_cover:
                        await DownloaderService.download_cover_by_platform_id(
                            platform_id, user_id=user_id
                        )

                except Exception as e:
                    logger.error(f"[yt-dlp] Background download failed: {e}")
                    await repo.update(
                        platform_id,
                        {
                            "video_download_status": DownloadStatus.FAILED.value,
                            "error_message": str(e)[:500],
                        },
                        user_id=user_id,
                    )

            import os

            background_tasks.add_task(
                _ytdlp_background_download,
                url,
                platform_id,
                auth.user_id,
                request.video_bool,
                request.music_bool,
                request.cover_bool,
            )
            logger.info(
                f"[yt-dlp] FastAPI background download tasks added: {platform_id}"
            )

    # Log action
    background_tasks.add_task(
        log_user_action,
        user_id=auth.user_id,
        action="fetch",
        message=f"Video parsed via yt-dlp ({platform}): {video_title[:30]}...",
        status="success",
        aweme_id=platform_id,
        details={"platform": platform, "parse_method": "ytdlp"},
    )

    # Handle datetime objects to string
    published_at = parsed_data.get("published_at")
    if published_at and hasattr(published_at, "isoformat"):
        published_at = published_at.isoformat()

    return {
        "success": True,
        "message": "Video processing task submitted",
        "parse_method": "ytdlp",
        "parse_method_name": f"yt-dlp ({platform})",
        "fallback_used": False,
        "fallback_reason": None,
        "id": save_result.get("id"),
        "platform_id": platform_id,
        "title": parsed_data.get("title"),
        "author": parsed_data.get("author"),
        "media_type": parsed_data.get("media_type"),
        "video_download_urls": parsed_data.get("video_download_urls", []),
        "cover_urls": parsed_data.get("cover_urls", []),
        "image_download_urls": [],
        "like_count": parsed_data.get("like_count", 0),
        "comment_count": parsed_data.get("comment_count", 0),
        "share_count": parsed_data.get("share_count", 0),
        "favorite_count": parsed_data.get("favorite_count", 0),
        "duration": parsed_data.get("duration", "0"),
        "published_at": published_at,
        "description": parsed_data.get("description"),
        "original_url": parsed_data.get("original_url"),
        "resolution": parsed_data.get("resolution"),
        "video_download_status": "PENDING",
        "download_task_id": download_task_id,
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
    """Redirect legacy /douyin/ routes to /videos/"""
    new_url = str(request.url).replace("/douyin/", "/videos/", 1)
    return RedirectResponse(url=new_url, status_code=308)
