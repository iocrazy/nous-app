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


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_media_task(
    self,
    platform_id: str,
    user_id: str,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
):
    """
    Download media files with progress tracking and TaskManager integration.

    This task runs after metadata is parsed and saved.
    Progress is tracked in Redis via TaskManager and can be polled by frontend.

    Args:
        platform_id: Video ID
        user_id: User ID
        download_video: Whether to download video
        download_music: Whether to download music
        download_cover: Whether to download cover
        media_type: Media type (0=video, 2/68=images)
        video_title: Video title for logging

    Returns:
        dict: Download result
    """
    task_id = self.request.id
    logger.info(f"[Celery] Starting download task {task_id} for {platform_id}")

    # Unified TaskTracker (Supabase)
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

    try:
        from app.celery_app import celery_app

        redis_client = celery_app.backend.client

        # Create unified progress tracker (Redis + TaskTracker)
        tracker = UnifiedProgressTracker(
            task_id=task_id,
            redis_client=redis_client,
            unified_tracker=tracker_unified,
            unified_task_id=unified_task_id,
        )

        # Execute downloads based on type
        results = {
            "video": None,
            "music": None,
            "cover": None,
        }
        video_result = None

        if int(media_type) in (0, 4, 61):  # Video types
            if download_video:
                logger.info(f"[Celery] Downloading video: {platform_id}")
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

            if download_music:
                logger.info(f"[Celery] Downloading music: {platform_id}")
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
                logger.info(f"[Celery] Downloading cover: {platform_id}")
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

        elif int(media_type) in (2, 68):  # Image types
            if download_video:  # "video" flag used for images too
                logger.info(f"[Celery] Downloading images: {platform_id}")
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

            if download_music:
                logger.info(f"[Celery] Downloading music: {platform_id}")
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
                logger.info(f"[Celery] Downloading cover: {platform_id}")
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

        # Check if video download was successful
        if video_result and hasattr(video_result, "video_download_status"):
            if video_result.video_download_status == DownloadStatus.COMPLETED:
                tracker.complete()
                if unified_task_id:
                    try:
                        run_async(tracker_unified.complete(unified_task_id))
                    except Exception:
                        pass
                logger.success(f"[Celery] Download task completed: {platform_id}")

                # Log success
                run_async(
                    log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"Download completed: {video_title[:30]}...",
                        status="success",
                        aweme_id=platform_id,
                        details={"media_type": media_type, "task_id": task_id},
                    )
                )

                # Auto-create resource record (dedup-aware)
                try:
                    from app.services.media_service import MediaService

                    run_async(
                        MediaService._create_resource_record(platform_id, user_id)
                    )
                except Exception as e:
                    logger.warning(
                        f"[Celery] Failed to create resource record for {platform_id}: {e}"
                    )

                # Chain HLS transcode for video files
                _maybe_chain_transcode(platform_id, user_id)

                # Chain AI pipeline if user has auto-transcribe enabled
                _maybe_chain_ai_pipeline(platform_id, user_id)

                return {
                    "status": "success",
                    "platform_id": platform_id,
                    "results": results,
                }
            else:
                # Download failed
                error_msg = (
                    video_result.error
                    if hasattr(video_result, "error") and video_result.error
                    else "Download failed"
                )
                raise Exception(error_msg)

        # No video result means video download was not requested, mark as complete
        tracker.complete()
        if unified_task_id:
            try:
                run_async(tracker_unified.complete(unified_task_id))
            except Exception:
                pass
        logger.success(f"[Celery] Download task completed (no video): {platform_id}")

        # Auto-create resource record (dedup-aware)
        try:
            from app.services.media_service import MediaService

            run_async(MediaService._create_resource_record(platform_id, user_id))
        except Exception as e:
            logger.warning(
                f"[Celery] Failed to create resource record for {platform_id}: {e}"
            )

        return {
            "status": "success",
            "platform_id": platform_id,
            "results": results,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"[Celery] Download task failed: {platform_id}, error: {error_msg}"
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

        # Use Celery's built-in retry mechanism
        if self.request.retries < self.max_retries:
            countdown = 30 * (2 ** self.request.retries)
            logger.info(
                f"[Celery] Scheduling retry {self.request.retries + 1}/3 for {platform_id} in {countdown}s"
            )
            raise self.retry(exc=e, countdown=countdown)

        # Log failure after max retries
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download failed: {video_title[:30]}...",
                status="error",
                aweme_id=platform_id,
                details={"error": error_msg[:200], "retry_count": self.request.retries},
            )
        )

        logger.error(f"[Celery] Max retries reached for {platform_id}")
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": error_msg,
            "retry_count": self.request.retries,
        }


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

        # Update unified TaskTracker (throttled at 1s internally)
        if self.unified_tracker and self.unified_task_id:
            try:
                run_async(self.unified_tracker.update_progress(
                    self.unified_task_id,
                    percent,
                    speed=int(self._speed),
                ))
            except Exception:
                pass

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


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_ytdlp_task(
    self,
    url: str,
    platform_id: str,
    user_id: str,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    video_title: str = "undefined",
):
    """
    Celery task for downloading media via yt-dlp (non-Douyin platforms).

    Args:
        url: Original video URL
        platform_id: Video platform ID
        user_id: User ID
        download_video: Whether to download video
        download_music: Whether to download audio
        download_cover: Whether to download cover (thumbnail)
        video_title: Video title for logging

    Returns:
        dict: Download result
    """
    task_id = self.request.id
    logger.info(f"[Celery/yt-dlp] Starting download task {task_id} for {platform_id}")

    # Unified TaskTracker (Supabase)
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

    try:
        from app.repositories.media_repository import MediaRepository
        from app.services.ytdlp_service import YtdlpService

        results = {"video": None, "music": None, "cover": None}

        # Detect platform from URL for structured path
        from app.services.url_router import URLRouter

        detected_platform, _ = URLRouter.detect_platform(url)
        storage_dir, relative_prefix = Utils.create_web_resource_path(
            detected_platform, platform_id
        )

        repo_class = MediaRepository

        # Create unified progress tracker (Redis + TaskTracker)
        from app.celery_app import celery_app
        redis_client = celery_app.backend.client

        tracker = UnifiedProgressTracker(
            task_id=task_id,
            redis_client=redis_client,
            unified_tracker=tracker_unified,
            unified_task_id=unified_task_id,
        )

        if download_video:
            logger.info(f"[Celery/yt-dlp] Downloading video: {platform_id}")

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
                repo = repo_class()
                run_async(
                    repo.mark_media_as_downloaded(
                        platform_id=platform_id,
                        download_path=relative_path,
                        duration=0,
                        storage_size=result.get("file_size", 0),
                    )
                )
                # Optimize for streaming
                run_async(
                    DownloaderService.optimize_video_for_streaming(result["file_path"])
                )
                results["video"] = DownloadStatus.COMPLETED.value
            else:
                results["video"] = DownloadStatus.FAILED.value

        if download_music:
            logger.info(f"[Celery/yt-dlp] Extracting audio: {platform_id}")
            result = run_async(
                YtdlpService.download_audio(url, str(storage_dir), platform_id)
            )
            if result.get("file_path"):
                repo = repo_class()
                run_async(repo.mark_music_as_downloaded(platform_id))
                results["music"] = DownloadStatus.COMPLETED.value
            else:
                results["music"] = DownloadStatus.FAILED.value

        # For cover: download thumbnail from metadata
        if download_cover:
            logger.info(f"[Celery/yt-dlp] Downloading cover: {platform_id}")
            repo = repo_class()
            run_async(
                DownloaderService.download_cover_by_platform_id(
                    platform_id, user_id=user_id
                )
            )
            results["cover"] = "attempted"

        tracker.complete()
        if unified_task_id:
            try:
                run_async(tracker_unified.complete(unified_task_id))
            except Exception:
                pass
        logger.success(f"[Celery/yt-dlp] Download task completed: {platform_id}")

        # Auto-create resource record (dedup-aware)
        try:
            from app.services.media_service import MediaService

            run_async(MediaService._create_resource_record(platform_id, user_id))
        except Exception as e:
            logger.warning(
                f"[Celery/yt-dlp] Failed to create resource record for {platform_id}: {e}"
            )

        # Chain HLS transcode for video files
        _maybe_chain_transcode(platform_id, user_id)

        # Log success
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download completed (yt-dlp): {video_title[:30]}...",
                status="success",
                aweme_id=platform_id,
                details={"media_type": "video", "task_id": task_id},
            )
        )

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
            f"[Celery/yt-dlp] Download task failed: {platform_id}, error: {error_msg}"
        )

        # Write failure to Redis so frontend polling picks it up
        try:
            tracker.failed(error_msg)
        except Exception:
            pass

        if unified_task_id:
            try:
                run_async(tracker_unified.fail(unified_task_id, error_msg))
            except Exception:
                pass

        if self.request.retries < self.max_retries:
            countdown = 30 * (2**self.request.retries)
            logger.info(
                f"[Celery/yt-dlp] Scheduling retry {self.request.retries + 1}/3 for {platform_id} in {countdown}s"
            )
            raise self.retry(exc=e, countdown=countdown)

        # Log failure after max retries
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download failed (yt-dlp): {video_title[:30]}...",
                status="error",
                aweme_id=platform_id,
                details={"error": error_msg[:200], "retry_count": self.request.retries},
            )
        )

        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": error_msg,
        }


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
