# app/tasks/download_tasks.py

"""
Download Tasks Module

Contains async download tasks for video, image sets, music, and covers.
Integrates with TaskManager for task status tracking and automatic retries.
"""

import asyncio

from celery import shared_task
from loguru import logger

from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.repositories.user_logs_repository import log_user_action
from app.services.downloader import DownloaderService


def run_async(coro):
    """Run async coroutine in synchronous environment"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If event loop is already running, create new task
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop, create new one
        return asyncio.run(coro)


def _maybe_chain_transcode(platform_id: str, user_id: str):
    """Chain HLS transcoding after download if the resource is a video."""
    try:
        from app.repositories.resources_repository import ResourcesRepository

        repo = ResourcesRepository()
        resource = run_async(repo.get_resource_by_platform_id(platform_id))
        if not resource:
            return

        mime = resource.get("mime_type", "")
        if not mime.startswith("video/"):
            return

        resource_id = str(resource["id"])
        versions = run_async(repo.get_versions(resource_id))
        if not versions:
            return

        # Transcode the latest version
        latest = versions[0]
        version_id = str(latest["id"])

        from app.tasks.transcode_tasks import maybe_trigger_transcode
        maybe_trigger_transcode(resource_id, version_id, mime, user_id=user_id)
    except Exception as e:
        logger.warning(f"[Celery] Failed to chain transcode for {platform_id}: {e}")


def _maybe_chain_ai_pipeline(platform_id: str, user_id: str):
    """Chain AI tasks after download if user has auto-transcribe/summarize enabled."""
    try:
        from app.repositories.user_settings_repository import UserSettingsRepository

        repo = UserSettingsRepository()
        settings = run_async(repo.get_by_user_id(user_id))

        ai_settings = {}
        if settings and settings.get("settings_json"):
            ai_settings = settings["settings_json"].get("ai_settings", {})

        transcript_bool = ai_settings.get("auto_transcribe", False)
        summary_bool = ai_settings.get("auto_summarize", False)

        if not transcript_bool and not summary_bool:
            logger.info(
                f"[AI] Auto-transcribe/summarize disabled for user {user_id}, skipping AI pipeline"
            )
            return

        # Look up resource_id from platform_id
        resource_id = None
        try:
            from app.repositories.resources_repository import ResourcesRepository

            res_repo = ResourcesRepository()
            resource = run_async(res_repo.get_resource_by_platform_id(platform_id))
            if resource:
                resource_id = str(resource["id"])
        except Exception as e:
            logger.debug(f"[AI] Could not resolve resource_id for {platform_id}: {e}")

        from app.tasks.ai_tasks import chain_ai_pipeline

        chain_ai_pipeline(
            platform_id=platform_id,
            user_id=user_id,
            resource_id=resource_id,
            transcript_bool=transcript_bool,
            summary_bool=summary_bool,
        )
        logger.info(
            f"[AI] Pipeline chained after download: {platform_id} "
            f"(transcribe={transcript_bool}, summarize={summary_bool}, resource={resource_id})"
        )

    except Exception as e:
        logger.warning(f"[AI] Failed to chain AI pipeline for {platform_id}: {e}")


class UnifiedProgressTracker:
    """Progress tracker that writes to Redis (real-time) + TaskTracker (Supabase lifecycle)."""

    def __init__(self, task_id: str, redis_client,
                 unified_tracker=None, unified_task_id=None):
        self.task_id = task_id
        self.redis = redis_client
        self.unified_tracker = unified_tracker
        self.unified_task_id = unified_task_id
        self.last_update = 0
        self._last_downloaded = 0
        self._last_time = 0
        self._speed = 0.0

    def update(self, downloaded: int, total: int):
        """Update download progress."""
        import json
        import time

        now = time.time()
        if now - self.last_update < 0.5:
            return
        self.last_update = now

        # Calculate speed
        if self._last_time > 0:
            time_diff = now - self._last_time
            if time_diff > 0:
                self._speed = (downloaded - self._last_downloaded) / time_diff
        self._last_downloaded = downloaded
        self._last_time = now

        percent = int((downloaded / total) * 100) if total > 0 else 0
        speed_str = self._format_speed(self._speed)

        # Write to Redis for real-time frontend polling
        progress_data = {
            "percent": percent,
            "downloaded": downloaded,
            "total": total,
            "speed": speed_str,
            "status": "downloading",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 3600, json.dumps(progress_data)
        )

        # Update unified TaskTracker (throttled at 1s internally).
        # When called from within a running event loop (e.g. inside an async
        # download function), use create_task() to avoid the overhead of
        # run_async() which would spawn a thread + new event loop per update.
        if self.unified_tracker and self.unified_task_id:
            coro = self.unified_tracker.update_progress(
                self.unified_task_id,
                percent,
                speed=int(self._speed),
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                # No running event loop — fall back to blocking run_async
                try:
                    run_async(coro)
                except Exception as e:
                    logger.debug(f"[ProgressTracker] Supabase update failed: {e}")

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed as human readable string."""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def complete(self):
        """Mark download as complete in Redis."""
        import json

        progress_data = {
            "percent": 100,
            "downloaded": 0,
            "total": 0,
            "speed": "0 B/s",
            "status": "completed",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 60, json.dumps(progress_data)
        )

    def failed(self, error: str):
        """Mark download as failed in Redis."""
        import json

        progress_data = {
            "percent": 0,
            "status": "failed",
            "error": error[:200] if error else "Unknown error",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 300, json.dumps(progress_data)
        )


# ─── Internal download strategies ─────────────────────────────────────

def _do_douyin_download(
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_music: bool,
    download_cover: bool,
    media_type: int,
    tracker: UnifiedProgressTracker,
) -> dict:
    """Douyin download strategy: reads URLs from DB, downloads via httpx."""
    results = {"video": None, "music": None, "cover": None}

    if int(media_type) in (0, 4, 61):  # Video types
        if download_video:
            logger.info(f"[Download] Downloading douyin video: {platform_id}")
            video_result = run_async(
                DownloaderService.download_video_by_platform_id(
                    platform_id, user_id=user_id, progress_tracker=tracker
                )
            )
            results["video"] = (
                video_result.video_download_status.value
                if hasattr(video_result, "video_download_status")
                else "unknown"
            )
            if results["video"] != "completed":
                error_msg = getattr(video_result, "error", None) or "Download failed"
                logger.warning(f"[Download] Video failed for {platform_id}: {error_msg}")

        if download_music:
            logger.info(f"[Download] Downloading douyin music: {platform_id}")
            result = run_async(
                DownloaderService.download_music_by_platform_id(
                    platform_id=platform_id, user_id=user_id
                )
            )
            results["music"] = (
                result.music_download_status.value
                if hasattr(result, "music_download_status")
                else "unknown"
            )

    elif int(media_type) in (2, 68):  # Image types
        if download_video:  # "video" flag used for images too
            logger.info(f"[Download] Downloading douyin images: {platform_id}")
            video_result = run_async(
                DownloaderService.download_images_by_platform_id(
                    platform_id, user_id=user_id
                )
            )
            results["video"] = (
                video_result.video_download_status.value
                if hasattr(video_result, "video_download_status")
                else "unknown"
            )
            if results["video"] != "completed":
                error_msg = getattr(video_result, "error", None) or "Image download failed"
                logger.warning(f"[Download] Images failed for {platform_id}: {error_msg}")

        if download_music:
            logger.info(f"[Download] Downloading douyin music: {platform_id}")
            result = run_async(
                DownloaderService.download_music_by_platform_id(
                    platform_id=platform_id, user_id=user_id
                )
            )
            results["music"] = (
                result.music_download_status.value
                if hasattr(result, "music_download_status")
                else "unknown"
            )

    if download_cover:
        logger.info(f"[Download] Downloading douyin cover: {platform_id}")
        result = run_async(
            DownloaderService.download_cover_by_platform_id(
                platform_id, user_id=user_id
            )
        )
        results["cover"] = (
            result.cover_download_status.value
            if hasattr(result, "cover_download_status")
            else "unknown"
        )

    return results


def _do_ytdlp_download(
    url: str,
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_music: bool,
    download_cover: bool,
    tracker: UnifiedProgressTracker,
) -> dict:
    """yt-dlp download strategy: downloads via yt-dlp using original URL."""
    from app.repositories.media_repository import MediaRepository
    from app.services.url_router import URLRouter
    from app.services.ytdlp_service import YtdlpService

    results = {"video": None, "music": None, "cover": None}

    detected_platform, _ = URLRouter.detect_platform(url)
    storage_dir, relative_prefix = Utils.create_web_resource_path(
        detected_platform, platform_id
    )

    if download_video:
        logger.info(f"[Download] Downloading video via yt-dlp: {platform_id}")

        def on_progress(downloaded: int, total: int, speed: str):
            tracker.update(downloaded, total)

        result = run_async(
            YtdlpService.download_video(
                url, str(storage_dir), platform_id, progress_callback=on_progress
            )
        )
        if result.get("file_path"):
            import os

            file_name = os.path.basename(result["file_path"])
            relative_path = f"{relative_prefix}/{file_name}"
            repo = MediaRepository()
            run_async(
                repo.mark_media_as_downloaded(
                    platform_id=platform_id,
                    download_path=relative_path,
                    duration=0,
                    storage_size=result.get("file_size", 0),
                )
            )
            run_async(
                DownloaderService.optimize_video_for_streaming(result["file_path"])
            )
            results["video"] = DownloadStatus.COMPLETED.value
        else:
            results["video"] = DownloadStatus.FAILED.value
            logger.warning(f"[Download] yt-dlp video failed for {platform_id}: no output file")

    if download_music:
        logger.info(f"[Download] Extracting audio via yt-dlp: {platform_id}")
        result = run_async(
            YtdlpService.download_audio(url, str(storage_dir), platform_id)
        )
        if result.get("file_path"):
            repo = MediaRepository()
            run_async(repo.mark_music_as_downloaded(platform_id))
            results["music"] = DownloadStatus.COMPLETED.value
        else:
            results["music"] = DownloadStatus.FAILED.value

    if download_cover:
        logger.info(f"[Download] Downloading cover: {platform_id}")
        cover_result = run_async(
            DownloaderService.download_cover_by_platform_id(
                platform_id, user_id=user_id
            )
        )
        if cover_result and cover_result.cover_download_status == DownloadStatus.COMPLETED:
            results["cover"] = DownloadStatus.COMPLETED.value
        else:
            error = cover_result.error if cover_result else "Unknown error"
            logger.warning(f"[Download] Cover download failed for {platform_id}: {error}")
            results["cover"] = DownloadStatus.FAILED.value

    return results


# ─── Unified download task ─────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_unified_task(
    self,
    platform_id: str,
    user_id: str,
    url: str = None,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: str = None,
    _dedup_key: str = None,       # Orchestrator dedup key
    _unified_task_id: str = None,  # Orchestrator task ID (for signals)
):
    """
    Unified download task for all platforms.

    Routing:
      - url=None  → Douyin path (reads download URLs from DB, downloads via httpx)
      - url given → yt-dlp path (downloads directly from URL)

    Args:
        platform_id: Media platform ID
        user_id: User ID
        url: Original URL (only for yt-dlp platforms; None for Douyin)
        download_video: Whether to download video
        download_music: Whether to download audio
        download_cover: Whether to download cover/thumbnail
        media_type: Media type (0=video, 2/68=images). Only used in Douyin path.
        video_title: Title for logging and task tracker display
        resource_id: User's resource record ID (for per-user status updates)
        _dedup_key: Orchestrator dedup key (for Redis lock management)
        _unified_task_id: Orchestrator task ID (for Celery signal hooks)
    """
    task_id = self.request.id
    strategy = "yt-dlp" if url else "douyin"
    logger.info(f"[Download/{strategy}] Starting task {task_id} for {platform_id}")

    # ── TaskTracker setup (Supabase lifecycle) ──
    from app.services.task_tracker import get_task_tracker
    tracker_unified = get_task_tracker()
    unified_task_id = None
    dl_parts = []
    if download_video:
        dl_parts.append("Video")
    if download_music:
        dl_parts.append("Audio")
    if download_cover:
        dl_parts.append("Cover")
    dl_subtitle = " + ".join(dl_parts) if dl_parts else None

    try:
        unified_task_id = run_async(tracker_unified.create(
            user_id=user_id,
            task_type="download",
            title=video_title or platform_id,
            subtitle=dl_subtitle,
            media_id=platform_id,
            celery_task_id=task_id,
        ))
        run_async(tracker_unified.start(unified_task_id))
    except Exception as e:
        logger.warning(f"[TaskTracker] Failed to create unified task: {e}")

    # Store orchestrator metadata for Celery signals
    if unified_task_id and _dedup_key:
        try:
            from app.services.task_orchestrator import get_orchestrator, TaskPhase
            orchestrator = get_orchestrator()
            # Update the unified_task row with dedup_key and phase
            client = run_async(orchestrator._get_client())
            run_async(
                client.table("unified_tasks").update({
                    "dedup_key": _dedup_key,
                    "phase": TaskPhase.DEDUP_CHECK.value,
                }).eq("id", unified_task_id).execute()
            )
        except Exception as e:
            logger.warning(f"[Orchestrator] Failed to set dedup_key: {e}")

    # Make unified_task_id available to signals via kwargs
    if unified_task_id:
        self.request.kwargs = self.request.kwargs or {}
        self.request.kwargs["_unified_task_id"] = unified_task_id
        if _dedup_key:
            self.request.kwargs["_dedup_key"] = _dedup_key

    try:
        # ── Progress tracker setup (Redis real-time) ──
        from app.celery_app import celery_app
        redis_client = celery_app.backend.client

        tracker = UnifiedProgressTracker(
            task_id=task_id,
            redis_client=redis_client,
            unified_tracker=tracker_unified,
            unified_task_id=unified_task_id,
        )

        # ── Check global cache: skip download if file already on server ──
        if resource_id:
            from app.repositories.media_repository import MediaRepository as _MR
            from app.repositories.resources_repository import ResourcesRepository as _RR
            _res_repo = _RR()
            _media_repo = _MR()
            global_media = run_async(_media_repo.get_by_platform_id(platform_id))

            if global_media:
                cache_updates = {}
                if download_video and global_media.get("video_download_status") == "completed":
                    cache_updates["video_download_status"] = "completed"
                if download_music and global_media.get("music_download_status") == "completed":
                    cache_updates["music_download_status"] = "completed"
                if download_cover and global_media.get("cover_download_status") == "completed":
                    cache_updates["cover_download_status"] = "completed"
                if download_video and int(media_type) in (2, 68) and global_media.get("image_download_status") == "completed":
                    cache_updates["image_download_status"] = "completed"

                if cache_updates:
                    run_async(_res_repo.update_download_status(resource_id, cache_updates))

                # If ALL requested types are cached, skip download entirely
                all_cached = True
                if download_video:
                    if int(media_type) in (2, 68):
                        all_cached = all_cached and global_media.get("image_download_status") == "completed"
                    else:
                        all_cached = all_cached and global_media.get("video_download_status") == "completed"
                if download_music:
                    all_cached = all_cached and global_media.get("music_download_status") == "completed"
                if download_cover:
                    all_cached = all_cached and global_media.get("cover_download_status") == "completed"

                if all_cached:
                    logger.info(f"[Download] All requested types cached for {platform_id}, skipping download")
                    tracker.complete()
                    if unified_task_id:
                        try:
                            run_async(tracker_unified.complete(unified_task_id))
                        except Exception:
                            pass
                    # Update resource file paths from global media
                    path_updates = {}
                    if global_media.get("download_path"):
                        path_updates["file_path"] = global_media["download_path"]
                    if global_media.get("cover_download_path"):
                        path_updates["cover_image_path"] = global_media["cover_download_path"]
                    if path_updates:
                        run_async(_res_repo.update_resource(resource_id, path_updates))
                    return {"status": "success", "platform_id": platform_id, "cache_hit": True}

        # ── Dispatch to strategy ──
        if url:
            results = _do_ytdlp_download(
                url=url,
                platform_id=platform_id,
                user_id=user_id,
                download_video=download_video,
                download_music=download_music,
                download_cover=download_cover,
                tracker=tracker,
            )
        else:
            results = _do_douyin_download(
                platform_id=platform_id,
                user_id=user_id,
                download_video=download_video,
                download_music=download_music,
                download_cover=download_cover,
                media_type=media_type,
                tracker=tracker,
            )

        # ── Common post-download: check partial failures ──
        has_failures = any(
            v not in (None, "completed")
            for v in results.values()
        )
        if has_failures:
            failed_parts = [k for k, v in results.items() if v not in (None, "completed")]
            warn_msg = f"Partial failure: {', '.join(failed_parts)} did not complete"
            logger.warning(f"[Download/{strategy}] {warn_msg}: {platform_id}")
            tracker.complete()
            if unified_task_id:
                try:
                    run_async(tracker_unified.fail(unified_task_id, warn_msg))
                except Exception:
                    pass
        else:
            tracker.complete()
            if unified_task_id:
                try:
                    run_async(tracker_unified.complete(unified_task_id))
                except Exception:
                    pass
            logger.success(f"[Download/{strategy}] Completed: {platform_id}")

        # Log success
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download completed ({strategy}): {video_title[:30]}...",
                status="success",
                aweme_id=platform_id,
                details={"media_type": media_type, "task_id": task_id},
            )
        )

        # ── Update user resource download statuses based on actual results ──
        if resource_id:
            try:
                from app.repositories.media_repository import MediaRepository as _MR2
                from app.repositories.resources_repository import ResourcesRepository as _RR2
                _res_repo2 = _RR2()
                status_updates = {}
                path_updates = {}

                if download_video:
                    video_result = results.get("video")
                    if int(media_type) in (2, 68):
                        status_updates["image_download_status"] = video_result if video_result == "completed" else "failed"
                    else:
                        status_updates["video_download_status"] = video_result if video_result == "completed" else "failed"
                if download_music:
                    music_result = results.get("music")
                    status_updates["music_download_status"] = music_result if music_result == "completed" else "failed"
                if download_cover:
                    cover_result = results.get("cover")
                    status_updates["cover_download_status"] = cover_result if cover_result == "completed" else "failed"

                # Also update file paths on resource
                fresh_media = run_async(_MR2().get_by_platform_id(platform_id))
                if fresh_media:
                    if fresh_media.get("download_path"):
                        path_updates["file_path"] = fresh_media["download_path"]
                    if fresh_media.get("cover_download_path"):
                        path_updates["cover_image_path"] = fresh_media["cover_download_path"]

                run_async(_res_repo2.update_resource(resource_id, {**status_updates, **path_updates}))
            except Exception as e:
                logger.warning(f"[Download] Failed to update resource status for {platform_id}: {e}")

        # Chain HLS transcode for video files
        _maybe_chain_transcode(platform_id, user_id)

        # Chain AI pipeline if user has auto-transcribe enabled
        _maybe_chain_ai_pipeline(platform_id, user_id)

        return {
            "status": "success",
            "platform_id": platform_id,
            "results": results,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"[Download/{strategy}] Failed: {platform_id}, error: {error_msg}"
        )

        # Write failure to Redis + TaskTracker
        try:
            tracker.failed(error_msg)
        except Exception:
            pass
        if unified_task_id:
            try:
                run_async(tracker_unified.fail(unified_task_id, error_msg))
            except Exception:
                pass

        # Update user resource status to failed
        if resource_id:
            try:
                from app.repositories.resources_repository import ResourcesRepository as _RR3
                _res_repo3 = _RR3()
                fail_updates = {}
                if download_video:
                    if int(media_type) in (2, 68):
                        fail_updates["image_download_status"] = "failed"
                    else:
                        fail_updates["video_download_status"] = "failed"
                if download_music:
                    fail_updates["music_download_status"] = "failed"
                if download_cover:
                    fail_updates["cover_download_status"] = "failed"
                run_async(_res_repo3.update_download_status(resource_id, fail_updates))
            except Exception:
                pass

        # Celery retry with exponential backoff
        if self.request.retries < self.max_retries:
            countdown = 30 * (2 ** self.request.retries)
            logger.info(
                f"[Download/{strategy}] Retry {self.request.retries + 1}/3 for {platform_id} in {countdown}s"
            )
            raise self.retry(exc=e, countdown=countdown)

        # Log failure after max retries
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download failed ({strategy}): {video_title[:30]}...",
                status="error",
                aweme_id=platform_id,
                details={"error": error_msg[:200], "retry_count": self.request.retries},
            )
        )

        logger.error(f"[Download/{strategy}] Max retries reached for {platform_id}")
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": error_msg,
            "retry_count": self.request.retries,
        }


# Backward-compatible aliases for existing call sites during migration
download_media_task = download_unified_task
download_ytdlp_task = download_unified_task


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_video_task(self, platform_id: str, user_id: str = None):
    """
    Celery task for downloading a single video

    Args:
        platform_id: Video ID
        user_id: User ID (for data isolation)

    Returns:
        dict: Download result
    """
    logger.info(f"[Celery] Starting video download task: {platform_id}")

    try:
        result = run_async(
            DownloaderService.download_video_by_platform_id(
                platform_id, user_id=user_id
            )
        )

        if result.video_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Video download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
                "path": result.video_path,
                "duration": result.download_duration,
            }
        else:
            logger.warning(
                f"[Celery] Video download failed: {platform_id}, error: {result.error}"
            )
            # Retry
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2**self.request.retries),  # Exponential backoff
            )

    except Exception as e:
        logger.error(
            f"[Celery] Video download task error: {platform_id}, error: {str(e)}"
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2**self.request.retries))
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": str(e),
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_images_task(self, platform_id: str, user_id: str = None):
    """
    Celery task for downloading image sets

    Args:
        platform_id: Video ID
        user_id: User ID (for data isolation)

    Returns:
        dict: Download result
    """
    logger.info(f"[Celery] Starting image set download task: {platform_id}")

    try:
        result = run_async(
            DownloaderService.download_images_by_platform_id(
                platform_id, user_id=user_id
            )
        )

        if result.video_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Image set download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
            }
        else:
            logger.warning(
                f"[Celery] Image set download failed: {platform_id}, error: {result.error}"
            )
            raise self.retry(
                exc=Exception(result.error), countdown=10 * (2**self.request.retries)
            )

    except Exception as e:
        logger.error(
            f"[Celery] Image set download task error: {platform_id}, error: {str(e)}"
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2**self.request.retries))
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": str(e),
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_music_task(self, platform_id: str, user_id: str = None):
    """
    Celery task for downloading music

    Args:
        platform_id: Video ID
        user_id: User ID (for data isolation)

    Returns:
        dict: Download result
    """
    logger.info(f"[Celery] Starting music download task: {platform_id}")

    try:
        result = run_async(
            DownloaderService.download_music_by_platform_id(
                platform_id=platform_id, user_id=user_id
            )
        )

        if result.music_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Music download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
                "path": result.music_path,
            }
        else:
            logger.warning(
                f"[Celery] Music download failed: {platform_id}, error: {result.error}"
            )
            raise self.retry(
                exc=Exception(result.error), countdown=10 * (2**self.request.retries)
            )

    except Exception as e:
        logger.error(
            f"[Celery] Music download task error: {platform_id}, error: {str(e)}"
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2**self.request.retries))
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": str(e),
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_cover_task(self, platform_id: str, user_id: str = None):
    """
    Celery task for downloading covers

    Args:
        platform_id: Video ID
        user_id: User ID (for data isolation)

    Returns:
        dict: Download result
    """
    logger.info(f"[Celery] Starting cover download task: {platform_id}")

    try:
        result = run_async(
            DownloaderService.download_cover_by_platform_id(
                platform_id, user_id=user_id
            )
        )

        if result.cover_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Cover download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
                "path": result.cover_path,
            }
        else:
            logger.warning(
                f"[Celery] Cover download failed: {platform_id}, error: {result.error}"
            )
            raise self.retry(
                exc=Exception(result.error), countdown=10 * (2**self.request.retries)
            )

    except Exception as e:
        logger.error(
            f"[Celery] Cover download task error: {platform_id}, error: {str(e)}"
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2**self.request.retries))
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": str(e),
        }
