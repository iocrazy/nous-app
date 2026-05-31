# backend/app/api/media_download_router.py

"""
Media Download Router

Endpoints for downloading video/cover/music files, managing pending/retry downloads.
"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.repositories.media_repository import MediaRepository
from app.repositories.user_logs_repository import log_user_action

router = APIRouter()

TAGS_DOWNLOAD = ["Download Management"]

# Map an audio file suffix (lowercased, e.g. ".flac") to its MIME content type.
_AUDIO_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def audio_content_type(suffix: str) -> str:
    """Return the MIME content type for an audio file suffix.

    ``suffix`` should be a lowercased extension including the dot
    (e.g. ``".flac"``). Unknown suffixes default to ``"audio/mpeg"``.
    """
    return _AUDIO_CONTENT_TYPES.get(suffix, "audio/mpeg")


class RetryDownloadRequest(BaseModel):
    """Retry download request — select which media to re-download."""

    video_bool: bool = True
    cover_bool: bool = False


@router.get("/pending", tags=TAGS_DOWNLOAD)
async def get_pending_downloads(auth: AuthDep, limit: int = Query(100, ge=1, le=500)):
    """
    Get pending downloads list

    Returns list of videos waiting to be downloaded.
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
    """
    from app.api.media_fetch_helpers import dedup_and_dispatch as _dedup_and_dispatch

    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_title = video.get("title", platform_id)[:30]
        media_type = video.get("media_type", 0)
        media_id = video.get("id")

        status_updates: dict = {"error_message": None}
        if request.video_bool:
            status_updates["video_download_status"] = DownloadStatus.PENDING.value
        if request.cover_bool:
            status_updates["cover_download_status"] = DownloadStatus.PENDING.value

        await repo.update(platform_id, status_updates)

        resource_id = None
        if media_id:
            from app.repositories.media_repository import MediaRepository
            from app.repositories.resources_repository import ResourcesRepository

            res_repo = ResourcesRepository()
            user_resource = await res_repo.get_resource_by_media_id_and_creator(
                media_id, auth.user_id
            )
            if user_resource:
                resource_id = user_resource.get("id")

            # PR-C: download statuses live on parsed_media. Mark
            # ``pending`` directly on the shared row instead of mirroring
            # to resources.
            pm_status_updates: dict = {}
            if request.video_bool:
                pm_status_updates["video_download_status"] = "pending"
            if request.cover_bool:
                pm_status_updates["cover_download_status"] = "pending"
            if pm_status_updates:
                await MediaRepository().update(platform_id, pm_status_updates)

        dispatch_result = await _dedup_and_dispatch(
            platform_id=platform_id,
            user_id=auth.user_id,
            resource_id=resource_id,
            media_type=int(media_type) if str(media_type).isdigit() else 0,
            video_title=video_title,
            download_video=request.video_bool,
            download_cover=request.cover_bool,
            background_tasks=background_tasks,
        )
        download_task_id = dispatch_result["task_id"]

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
            "task_id": (
                dispatch_result.get("unified_task_id")
                if isinstance(dispatch_result, dict)
                else download_task_id
            ),
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

    Return video file for browser download.
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

        video_title = video.get("title", platform_id)
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
    """
    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")

        # Two possible sources (in order of preference):
        #   1. extract_audio_path — produced by ffmpeg audio extraction from
        #      the downloaded video (fast, no separate network call)
        #   2. music_download_path — separate BGM fetched via music URL
        # The gating status check was only on music_download_status, so once
        # a user extracted audio (which only writes extract_audio_path) the
        # endpoint still returned 404. Check both paths before falling back
        # to status + disk scan.
        extract_audio_path = video.get("extract_audio_path", "")
        music_download_path = video.get("music_download_path", "")
        audio_file = None

        for rel in (extract_audio_path, music_download_path):
            if not rel:
                continue
            candidate = Path(base_path) / rel
            if candidate.exists():
                audio_file = candidate
                break

        if not audio_file:
            music_status = video.get("music_download_status", "").lower()
            if music_status not in ("completed", "skipped"):
                raise HTTPException(
                    status_code=404,
                    detail="Audio file has not been prepared (no extracted or downloaded audio)",
                )

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
                for name in [
                    f"{platform_id}_audio",
                    "music",
                    f"{platform_id}_music",
                    "audio",
                ]:
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

        content_type = audio_content_type(suffix)

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


@router.get("/download/{platform_id}/gallery", tags=TAGS_DOWNLOAD)
async def download_gallery_zip(platform_id: str, auth: AuthDep):
    """Stream a zip containing every slide file for a gallery / image-text
    post. Works whether the slides folder contains images, videos (动图),
    or a mix — anything in slides/ is included. Falls back to the media
    root directory if no slides/ subfolder exists.

    Previously the frontend iterated image_download_urls on the client
    and downloaded each file separately (one at a time, exposing expiring
    CDN tokens, and silently dropping anything that wasn't an image).
    Streaming a zip from the backend hits the downloaded-to-disk copy
    once, and gives the user one file instead of N prompts.
    """
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    try:
        repo = MediaRepository()
        video = await repo.get_by_platform_id(platform_id)
        if not video:
            raise HTTPException(status_code=404, detail="Media not found")

        try:
            base_path = Utils.get_download_base_path()
        except ValueError:
            raise HTTPException(status_code=404, detail="Download path not configured")

        download_path = video.get("download_path", "")
        # download_path for gallery posts is usually the slides file itself
        # (`.../slides/001.jpg`) or the gallery root. Normalize to directory.
        candidate_dir = None
        if download_path:
            p = Path(base_path) / download_path
            if p.is_dir():
                candidate_dir = p
            elif p.is_file():
                candidate_dir = p.parent
            else:
                candidate_dir = p.parent if p.parent.exists() else None

        if not candidate_dir or not candidate_dir.exists():
            # Fallback: search by pattern
            for pattern in (
                f"global/resources/web/*/{video.get('id')}",
                f"global/resources/web/*/{platform_id}",
                f"*/{platform_id}",
            ):
                matches = list(Path(base_path).glob(pattern))
                if matches:
                    candidate_dir = matches[0]
                    break

        if not candidate_dir or not candidate_dir.exists():
            raise HTTPException(status_code=404, detail="Gallery folder not found")

        slides_dir = candidate_dir / "slides"
        if not slides_dir.exists() or not slides_dir.is_dir():
            slides_dir = candidate_dir

        slide_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"}
        files = sorted(
            [
                f
                for f in slides_dir.iterdir()
                if f.is_file() and f.suffix.lower() in slide_exts
            ]
        )
        if not files:
            raise HTTPException(status_code=404, detail="No gallery files on disk")

        video_title = video.get("title", platform_id) or platform_id
        safe_title = (
            "".join(
                c for c in video_title if c.isalnum() or c in (" ", "-", "_", ".")
            ).strip()
            or platform_id
        )
        zip_name = f"{safe_title[:80]}_gallery.zip"

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_name}"',
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to zip gallery for {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to package gallery")
