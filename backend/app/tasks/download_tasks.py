# app/tasks/download_tasks.py

"""
Download Tasks Module

Contains async download tasks for video, image sets, music, and covers.
Integrates with TaskManager for task status tracking and automatic retries.
"""

import asyncio
from celery import shared_task
from loguru import logger

from app.services.downloader import DownloaderService
from app.core.enums import DownloadStatus


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

    # Initialize TaskManager
    from app.services.task_manager import get_task_manager
    task_manager = get_task_manager()

    # Create or get task in TaskManager
    task = task_manager.get_task(platform_id)
    if not task:
        task = task_manager.create_task(platform_id, video_title)

    # Mark task as downloading
    task_manager.start_download(platform_id)

    try:
        from app.celery_app import celery_app
        from app.services.download_progress import DownloadProgressTracker

        # Get Redis client from Celery backend
        redis_client = celery_app.backend.client

        # Create progress tracker with TaskManager integration
        tracker = DownloadProgressTrackerWithTaskManager(
            task_id=task_id,
            platform_id=platform_id,
            redis_client=redis_client,
            task_manager=task_manager,
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
                        platform_id,
                        user_id=user_id,
                        progress_tracker=tracker
                    )
                )
                results["video"] = video_result.video_download_status.value if hasattr(video_result, 'video_download_status') else "unknown"

            if download_music:
                logger.info(f"[Celery] Downloading music: {platform_id}")
                result = run_async(
                    DownloaderService.download_music_by_platform_id(platform_id=platform_id, user_id=user_id)
                )
                results["music"] = result.music_download_status.value if hasattr(result, 'music_download_status') else "unknown"

            if download_cover:
                logger.info(f"[Celery] Downloading cover: {platform_id}")
                result = run_async(
                    DownloaderService.download_cover_by_platform_id(platform_id, user_id=user_id)
                )
                results["cover"] = result.cover_download_status.value if hasattr(result, 'cover_download_status') else "unknown"

        elif int(media_type) in (2, 68):  # Image types
            if download_video:  # "video" flag used for images too
                logger.info(f"[Celery] Downloading images: {platform_id}")
                video_result = run_async(
                    DownloaderService.download_images_by_platform_id(platform_id, user_id=user_id)
                )
                results["video"] = video_result.video_download_status.value if hasattr(video_result, 'video_download_status') else "unknown"

            if download_music:
                logger.info(f"[Celery] Downloading music: {platform_id}")
                result = run_async(
                    DownloaderService.download_music_by_platform_id(platform_id=platform_id, user_id=user_id)
                )
                results["music"] = result.music_download_status.value if hasattr(result, 'music_download_status') else "unknown"

            if download_cover:
                logger.info(f"[Celery] Downloading cover: {platform_id}")
                result = run_async(
                    DownloaderService.download_cover_by_platform_id(platform_id, user_id=user_id)
                )
                results["cover"] = result.cover_download_status.value if hasattr(result, 'cover_download_status') else "unknown"

        # Check if video download was successful
        if video_result and hasattr(video_result, 'video_download_status'):
            if video_result.video_download_status == DownloadStatus.COMPLETED:
                # Mark complete in TaskManager
                task_manager.complete_task(platform_id)
                tracker.complete()
                logger.success(f"[Celery] Download task completed: {platform_id}")
                return {
                    "status": "success",
                    "platform_id": platform_id,
                    "results": results,
                }
            else:
                # Download failed
                error_msg = video_result.error if hasattr(video_result, 'error') and video_result.error else "Download failed"
                raise Exception(error_msg)

        # No video result means video download was not requested, mark as complete
        task_manager.complete_task(platform_id)
        tracker.complete()
        logger.success(f"[Celery] Download task completed (no video): {platform_id}")
        return {
            "status": "success",
            "platform_id": platform_id,
            "results": results,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Celery] Download task failed: {platform_id}, error: {error_msg}")

        # Mark as failed in TaskManager
        task_manager.fail_task(platform_id, error_msg)

        # Check if we should retry
        current_task = task_manager.get_task(platform_id)
        retry_count = current_task.get("retry_count", 0) if current_task else 0

        if retry_count < 3:
            # Auto retry with exponential backoff
            countdown = 30 * (2 ** retry_count)
            logger.info(f"[Celery] Scheduling retry {retry_count + 1}/3 for {platform_id} in {countdown}s")
            raise self.retry(exc=e, countdown=countdown)

        logger.error(f"[Celery] Max retries reached for {platform_id}")
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": error_msg,
            "retry_count": retry_count,
        }


class DownloadProgressTrackerWithTaskManager:
    """Progress tracker that updates both the old format and TaskManager."""

    def __init__(self, task_id: str, platform_id: str, redis_client, task_manager):
        self.task_id = task_id
        self.platform_id = platform_id
        self.redis = redis_client
        self.task_manager = task_manager
        self.last_update = 0

    def update(self, downloaded: int, total: int):
        """Update download progress."""
        import time
        import json

        # Throttle updates to avoid Redis spam
        now = time.time()
        if now - self.last_update < 0.5:  # Update at most every 0.5s
            return
        self.last_update = now

        percent = int((downloaded / total) * 100) if total > 0 else 0
        speed = self._calculate_speed(downloaded)

        # Update old format for backward compatibility
        progress_data = {
            "percent": percent,
            "downloaded": downloaded,
            "total": total,
            "speed": speed,
            "status": "downloading",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}",
            3600,
            json.dumps(progress_data)
        )

        # Update TaskManager
        self.task_manager.update_progress(self.platform_id, downloaded, total, speed)

    def _calculate_speed(self, downloaded: int) -> str:
        """Calculate download speed string."""
        # Simple speed calculation (could be improved with time tracking)
        if downloaded < 1024:
            return f"{downloaded} B/s"
        elif downloaded < 1024 * 1024:
            return f"{downloaded / 1024:.1f} KB/s"
        else:
            return f"{downloaded / (1024 * 1024):.1f} MB/s"

    def complete(self):
        """Mark download as complete."""
        import json
        progress_data = {
            "percent": 100,
            "downloaded": 0,
            "total": 0,
            "speed": "0 B/s",
            "status": "completed",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}",
            3600,
            json.dumps(progress_data)
        )


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
            DownloaderService.download_video_by_platform_id(platform_id, user_id=user_id)
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
            logger.warning(f"[Celery] Video download failed: {platform_id}, error: {result.error}")
            # Retry
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)  # Exponential backoff
            )

    except Exception as e:
        logger.error(f"[Celery] Video download task error: {platform_id}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
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
            DownloaderService.download_images_by_platform_id(platform_id, user_id=user_id)
        )

        if result.video_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Image set download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
            }
        else:
            logger.warning(f"[Celery] Image set download failed: {platform_id}, error: {result.error}")
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)
            )

    except Exception as e:
        logger.error(f"[Celery] Image set download task error: {platform_id}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
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
            DownloaderService.download_music_by_platform_id(platform_id=platform_id, user_id=user_id)
        )

        if result.music_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Music download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
                "path": result.music_path,
            }
        else:
            logger.warning(f"[Celery] Music download failed: {platform_id}, error: {result.error}")
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)
            )

    except Exception as e:
        logger.error(f"[Celery] Music download task error: {platform_id}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
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
            DownloaderService.download_cover_by_platform_id(platform_id, user_id=user_id)
        )

        if result.cover_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] Cover download successful: {platform_id}")
            return {
                "status": "success",
                "platform_id": platform_id,
                "path": result.cover_path,
            }
        else:
            logger.warning(f"[Celery] Cover download failed: {platform_id}, error: {result.error}")
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)
            )

    except Exception as e:
        logger.error(f"[Celery] Cover download task error: {platform_id}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": str(e),
        }
