# backend/app/api/media_router.py

"""
Media Router

Parsed media processing API endpoints based on Supabase.
Requires authentication (JWT or API Key).

Sub-routers:
- media_fetch_router   — fetch/parse/batch endpoints
- media_download_router — file download & retry endpoints
- media_slides_router   — slides/audio serve endpoints
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel

from app.api.media_download_router import router as download_router

# Import sub-routers
from app.api.media_fetch_router import router as fetch_router
from app.api.media_slides_router import router as slides_router
from app.api.media_soda_router import router as soda_router
from app.core.deps import AuthDep
from app.core.enums import DownloadStatus
from app.repositories.media_repository import MediaRepository
from app.repositories.user_logs_repository import UserLogsRepository, log_user_action

router = APIRouter(prefix="/media")

# Include fetch and download sub-routers
router.include_router(fetch_router)
router.include_router(download_router)
router.include_router(soda_router)

# API group tags
TAGS_VIDEOS = ["Video Management"]
TAGS_STATS = ["Statistics"]
TAGS_LOGS = ["Logs"]


# ============================================
# Request/Response models
# ============================================


class MediaSearchRequest(BaseModel):
    """Video search request"""

    keyword: Optional[str] = None
    author: Optional[str] = None
    status: Optional[str] = None
    media_type: Optional[str] = None
    category: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


# ============================================
# Route endpoints — Video CRUD
# ============================================


@router.post("/cleanup-stale-downloads", tags=TAGS_VIDEOS)
async def cleanup_stale_downloads(
    auth: AuthDep,
    timeout_minutes: int = Query(30, ge=5, le=120),
):
    """
    Mark downloads stuck in 'downloading' state as 'failed'.
    """
    try:
        repo = MediaRepository()
        count = await repo.mark_stale_downloads_failed(timeout_minutes)
        return {"success": True, "cleaned": count}
    except Exception as e:
        logger.error(f"Stale download cleanup failed: {e}")
        raise HTTPException(status_code=500, detail="Cleanup failed")


@router.get("", tags=TAGS_VIDEOS)
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


@router.get("/{platform_id}", tags=TAGS_VIDEOS)
async def get_video(platform_id: str, auth: AuthDep):
    """
    Get video details

    Get full info of a single video by platform_id.
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)
        if not video:
            video = await repo.get_by_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        media_id = video.get("id")
        if media_id:
            from app.repositories.resources_repository import ResourcesRepository

            res_repo = ResourcesRepository()
            user_resource = await res_repo.get_resource_by_media_id_and_creator(
                media_id, auth.user_id
            )
            if user_resource:
                video["resource_id"] = user_resource["id"]
                for field in (
                    "video_download_status",
                    "music_download_status",
                    "cover_download_status",
                    "image_download_status",
                ):
                    user_status = user_resource.get(field)
                    if user_status is not None:
                        video[field] = user_status

        return {"success": True, "video": video}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get video details: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video details")


@router.delete("/{platform_id}", tags=TAGS_VIDEOS)
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
    """
    try:
        repo = MediaRepository()

        video = await repo.get_by_platform_id(platform_id)
        if not video:
            video = await repo.get_by_id(platform_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_title = video.get("title", platform_id)[:30] if video else platform_id
        files_deleted = []

        if delete_files:
            import shutil

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

            cover_path = video.get("cover_download_path")
            if cover_path:
                path = Path(cover_path)
                if path.exists():
                    path.unlink()
                    files_deleted.append(f"cover: {path.name}")

        result = await repo.delete(platform_id)

        if not result:
            raise HTTPException(
                status_code=404, detail="Failed to delete database record"
            )

        log_message = f"Deleted video: {video_title}..."
        if files_deleted:
            log_message += f" (deleted {len(files_deleted)} local files)"

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


@router.post("/search", tags=TAGS_VIDEOS)
async def search_videos(
    request: MediaSearchRequest,
    auth: AuthDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Search videos

    Multi-criteria search of stored videos.
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
    """
    try:
        repo = MediaRepository()
        stats = await repo.get_statistics(user_id=auth.user_id)
        return {"success": True, "statistics": stats}
    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")


@router.get("/logs", tags=TAGS_LOGS)
async def get_user_logs(
    auth: AuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    level: Optional[str] = Query(None, description="Filter by status level"),
    date_range: Optional[str] = Query(
        None, description="Preset date range: 24h, 7days, 30days, 90days"
    ),
    start_date: Optional[str] = Query(None, description="Custom start date (ISO)"),
    end_date: Optional[str] = Query(None, description="Custom end date (ISO)"),
    search: Optional[str] = Query(None, description="Search in message"),
):
    """
    Get user action logs (paginated)
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


# ============================================
# Slides/audio router — registered separately in __init__.py as media_content_router
# Original: APIRouter(prefix="/media") containing /{media_id}/slides and /{media_id}/audio
# ============================================

media_content_router = APIRouter(prefix="/media")
media_content_router.include_router(slides_router)


# ============================================
# Legacy redirect: /douyin/* -> /videos/*
# ============================================

legacy_router = APIRouter(prefix="/douyin")


@legacy_router.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False
)
async def legacy_douyin_redirect(path: str, request: Request):
    """Redirect legacy /douyin/ routes to /media/"""
    new_url = str(request.url).replace("/douyin/", "/videos/", 1)
    return RedirectResponse(url=new_url, status_code=308)
