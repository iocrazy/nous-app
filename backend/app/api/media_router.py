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
from app.core.scope_dep import ScopedRequestDep
from app.core.utils import Utils
from app.repositories.media_repository import MediaRepository
from app.repositories.user_logs_repository import (
    get_user_logs_repository,
    log_user_action,
)
from app.services.library.media_storage import ObjectStore, resolve_media_source
from app.services.library.object_gc import delete_object_if_unreferenced

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
async def get_video(platform_id: str, auth: AuthDep, _scope: ScopedRequestDep):
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

            async def _delete_stored_file(raw_path: str, label: str) -> None:
                """删除单个存储对象/文件，感知 sb:// 对象存储 vs 本地文件系统。

                任何一种删除失败都只记警告、不让整个删除端点 500——DB 记录
                删除仍要继续（对象/文件泄漏比"删不掉记录"轻，且可重试）。
                """
                loc = resolve_media_source(raw_path)
                if loc.is_object_store:
                    # C2: a prefix location (album — key ends in "/", e.g.
                    # t{scope}/album/{rid}/) has no single object at
                    # ``loc.key``. A plain ``remove()`` targets a key that was
                    # never PUT (the slides/audio/cover objects live UNDER
                    # the prefix), so it silently deletes nothing while the
                    # SDK call itself still "succeeds" — the delete endpoint
                    # reports done, the album stays on S3 forever.
                    # remove_prefix lists then bulk-removes every object
                    # actually under the prefix. Prefixes are namespaced by
                    # id and never shared, so no reference check is needed.
                    if loc.is_prefix:
                        try:
                            n = await ObjectStore(loc.bucket).remove_prefix(loc.key)
                            files_deleted.append(
                                f"album prefix: {loc.key} ({n} objects)"
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to delete object store {label} "
                                f"{loc.bucket}/{loc.key}: {e}"
                            )
                        return

                    # Single (content-addressed) key: this raw_path can be
                    # the SAME S3 object a `resources` row still serves
                    # (measured 2026-08-03: 991 groups of
                    # parsed_media.download_path <-> resources.file_path share
                    # a key) — an unconditional remove() here used to destroy
                    # that resource's file out from under it. Route through
                    # the reference-safe primitive instead; exclude this
                    # video's own row since the parsed_media DELETE below
                    # hasn't run yet (it would otherwise see its own row as a
                    # live "reference" and never actually delete anything).
                    pm_id = video.get("id")
                    outcome = await delete_object_if_unreferenced(
                        raw_path,
                        exclude={"parsed_media": [pm_id]} if pm_id else None,
                    )
                    if outcome == "deleted":
                        files_deleted.append(f"object: {loc.key}")
                    elif outcome == "kept_referenced":
                        logger.info(
                            f"Skipped delete for {label} {loc.bucket}/{loc.key}: "
                            "still referenced by another row"
                        )
                    # "noop" — storage-call or reference-check failure;
                    # object_gc already logged a warning.
                    return

                # 补 base_path join：旧代码 `Path(download_path)` 完全没 join
                # base_path，长期删的大概率是错的相对路径（除非 CWD 恰好等于
                # DOWNLOAD_PATH）。get_download_base_path 在未配置下载路径时
                # 会抛 ValueError——全 S3 化之后这完全可能发生，不能让它 500。
                try:
                    base_path = Utils.get_download_base_path()
                except ValueError as e:
                    logger.warning(
                        f"Skip filesystem delete for {label} {raw_path!r}: {e}"
                    )
                    return

                full = Path(base_path) / (loc.rel_path or raw_path)
                if full.exists():
                    if full.is_dir():
                        shutil.rmtree(full)
                        files_deleted.append(f"directory: {full.name}")
                    else:
                        full.unlink()
                        files_deleted.append(f"{label}: {full.name}")

            download_path = video.get("download_path")
            if download_path:
                await _delete_stored_file(download_path, "file")

            cover_path = video.get("cover_download_path")
            if cover_path:
                await _delete_stored_file(cover_path, "cover")

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
        repo = get_user_logs_repository()
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
