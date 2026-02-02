# app/tasks/download_tasks.py

"""
下载任务模块

包含视频、图集、音乐、封面的异步下载任务。
集成 TaskManager 进行任务状态追踪和自动重试。
"""

import asyncio
from celery import shared_task
from loguru import logger

from app.services.downloader import DownloaderService
from app.core.enums import DownloadStatus


def run_async(coro):
    """在同步环境中运行异步协程"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果事件循环已运行，创建新任务
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # 没有事件循环，创建新的
        return asyncio.run(coro)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_media_task(
    self,
    aweme_id: str,
    user_id: str,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    aweme_type: int = 0,
    video_title: str = "undefined",
):
    """
    Download media files with progress tracking and TaskManager integration.

    This task runs after metadata is parsed and saved.
    Progress is tracked in Redis via TaskManager and can be polled by frontend.

    Args:
        aweme_id: Video ID
        user_id: User ID
        download_video: Whether to download video
        download_music: Whether to download music
        download_cover: Whether to download cover
        aweme_type: Media type (0=video, 2/68=images)
        video_title: Video title for logging

    Returns:
        dict: Download result
    """
    task_id = self.request.id
    logger.info(f"[Celery] Starting download task {task_id} for {aweme_id}")

    # Initialize TaskManager
    from app.services.task_manager import get_task_manager
    task_manager = get_task_manager()

    # Create or get task in TaskManager
    task = task_manager.get_task(aweme_id)
    if not task:
        task = task_manager.create_task(aweme_id, video_title)

    # Mark task as downloading
    task_manager.start_download(aweme_id)

    try:
        from app.celery_app import celery_app
        from app.services.download_progress import DownloadProgressTracker

        # Get Redis client from Celery backend
        redis_client = celery_app.backend.client

        # Create progress tracker with TaskManager integration
        tracker = DownloadProgressTrackerWithTaskManager(
            task_id=task_id,
            aweme_id=aweme_id,
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

        if int(aweme_type) in (0, 4, 61):  # Video types
            if download_video:
                logger.info(f"[Celery] Downloading video: {aweme_id}")
                video_result = run_async(
                    DownloaderService.download_video_by_aweme_id(
                        aweme_id,
                        user_id=user_id,
                        progress_tracker=tracker
                    )
                )
                results["video"] = video_result.video_download_status.value if hasattr(video_result, 'video_download_status') else "unknown"

            if download_music:
                logger.info(f"[Celery] Downloading music: {aweme_id}")
                result = run_async(
                    DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id)
                )
                results["music"] = result.music_download_status.value if hasattr(result, 'music_download_status') else "unknown"

            if download_cover:
                logger.info(f"[Celery] Downloading cover: {aweme_id}")
                result = run_async(
                    DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["cover"] = result.cover_download_status.value if hasattr(result, 'cover_download_status') else "unknown"

        elif int(aweme_type) in (2, 68):  # Image types
            if download_video:  # "video" flag used for images too
                logger.info(f"[Celery] Downloading images: {aweme_id}")
                video_result = run_async(
                    DownloaderService.download_images_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["video"] = video_result.video_download_status.value if hasattr(video_result, 'video_download_status') else "unknown"

            if download_music:
                logger.info(f"[Celery] Downloading music: {aweme_id}")
                result = run_async(
                    DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id)
                )
                results["music"] = result.music_download_status.value if hasattr(result, 'music_download_status') else "unknown"

            if download_cover:
                logger.info(f"[Celery] Downloading cover: {aweme_id}")
                result = run_async(
                    DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["cover"] = result.cover_download_status.value if hasattr(result, 'cover_download_status') else "unknown"

        # Check if video download was successful
        if video_result and hasattr(video_result, 'video_download_status'):
            if video_result.video_download_status == DownloadStatus.COMPLETED:
                # Mark complete in TaskManager
                task_manager.complete_task(aweme_id)
                tracker.complete()
                logger.success(f"[Celery] Download task completed: {aweme_id}")
                return {
                    "status": "success",
                    "aweme_id": aweme_id,
                    "results": results,
                }
            else:
                # Download failed
                error_msg = video_result.error if hasattr(video_result, 'error') and video_result.error else "Download failed"
                raise Exception(error_msg)

        # No video result means video download was not requested, mark as complete
        task_manager.complete_task(aweme_id)
        tracker.complete()
        logger.success(f"[Celery] Download task completed (no video): {aweme_id}")
        return {
            "status": "success",
            "aweme_id": aweme_id,
            "results": results,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Celery] Download task failed: {aweme_id}, error: {error_msg}")

        # Mark as failed in TaskManager
        task_manager.fail_task(aweme_id, error_msg)

        # Check if we should retry
        current_task = task_manager.get_task(aweme_id)
        retry_count = current_task.get("retry_count", 0) if current_task else 0

        if retry_count < 3:
            # Auto retry with exponential backoff
            countdown = 30 * (2 ** retry_count)
            logger.info(f"[Celery] Scheduling retry {retry_count + 1}/3 for {aweme_id} in {countdown}s")
            raise self.retry(exc=e, countdown=countdown)

        logger.error(f"[Celery] Max retries reached for {aweme_id}")
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": error_msg,
            "retry_count": retry_count,
        }


class DownloadProgressTrackerWithTaskManager:
    """Progress tracker that updates both the old format and TaskManager."""

    def __init__(self, task_id: str, aweme_id: str, redis_client, task_manager):
        self.task_id = task_id
        self.aweme_id = aweme_id
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
        self.task_manager.update_progress(self.aweme_id, downloaded, total, speed)

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
def download_video_task(self, aweme_id: str, user_id: str = None):
    """
    下载单个视频的 Celery 任务

    Args:
        aweme_id: 视频 ID
        user_id: 用户 ID（用于数据隔离）

    Returns:
        dict: 下载结果
    """
    logger.info(f"[Celery] 开始下载视频任务: {aweme_id}")

    try:
        result = run_async(
            DownloaderService.download_video_by_aweme_id(aweme_id, user_id=user_id)
        )

        if result.video_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] 视频下载成功: {aweme_id}")
            return {
                "status": "success",
                "aweme_id": aweme_id,
                "path": result.video_path,
                "duration": result.download_duration,
            }
        else:
            logger.warning(f"[Celery] 视频下载失败: {aweme_id}, 错误: {result.error}")
            # 重试
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)  # 指数退避
            )

    except Exception as e:
        logger.error(f"[Celery] 视频下载任务异常: {aweme_id}, 错误: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": str(e),
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_images_task(self, aweme_id: str, user_id: str = None):
    """
    下载图集的 Celery 任务

    Args:
        aweme_id: 视频 ID
        user_id: 用户 ID（用于数据隔离）

    Returns:
        dict: 下载结果
    """
    logger.info(f"[Celery] 开始下载图集任务: {aweme_id}")

    try:
        result = run_async(
            DownloaderService.download_images_by_aweme_id(aweme_id, user_id=user_id)
        )

        if result.video_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] 图集下载成功: {aweme_id}")
            return {
                "status": "success",
                "aweme_id": aweme_id,
            }
        else:
            logger.warning(f"[Celery] 图集下载失败: {aweme_id}, 错误: {result.error}")
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)
            )

    except Exception as e:
        logger.error(f"[Celery] 图集下载任务异常: {aweme_id}, 错误: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": str(e),
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_music_task(self, aweme_id: str, user_id: str = None):
    """
    下载音乐的 Celery 任务

    Args:
        aweme_id: 视频 ID
        user_id: 用户 ID（用于数据隔离）

    Returns:
        dict: 下载结果
    """
    logger.info(f"[Celery] 开始下载音乐任务: {aweme_id}")

    try:
        result = run_async(
            DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id)
        )

        if result.music_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] 音乐下载成功: {aweme_id}")
            return {
                "status": "success",
                "aweme_id": aweme_id,
                "path": result.music_path,
            }
        else:
            logger.warning(f"[Celery] 音乐下载失败: {aweme_id}, 错误: {result.error}")
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)
            )

    except Exception as e:
        logger.error(f"[Celery] 音乐下载任务异常: {aweme_id}, 错误: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": str(e),
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def download_cover_task(self, aweme_id: str, user_id: str = None):
    """
    下载封面的 Celery 任务

    Args:
        aweme_id: 视频 ID
        user_id: 用户 ID（用于数据隔离）

    Returns:
        dict: 下载结果
    """
    logger.info(f"[Celery] 开始下载封面任务: {aweme_id}")

    try:
        result = run_async(
            DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id)
        )

        if result.cover_download_status == DownloadStatus.COMPLETED:
            logger.success(f"[Celery] 封面下载成功: {aweme_id}")
            return {
                "status": "success",
                "aweme_id": aweme_id,
                "path": result.cover_path,
            }
        else:
            logger.warning(f"[Celery] 封面下载失败: {aweme_id}, 错误: {result.error}")
            raise self.retry(
                exc=Exception(result.error),
                countdown=10 * (2 ** self.request.retries)
            )

    except Exception as e:
        logger.error(f"[Celery] 封面下载任务异常: {aweme_id}, 错误: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": str(e),
        }
