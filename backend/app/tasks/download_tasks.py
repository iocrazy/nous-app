# app/tasks/download_tasks.py

"""
下载任务模块

包含视频、图集、音乐、封面的异步下载任务。
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
    Download media files with progress tracking.

    This task runs after metadata is parsed and saved.
    Progress is tracked in Redis and can be polled by frontend.

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

    try:
        from app.celery_app import celery_app
        from app.services.download_progress import DownloadProgressTracker

        # Get Redis client from Celery backend
        redis_client = celery_app.backend.client

        # Create progress tracker
        tracker = DownloadProgressTracker(
            task_id=task_id,
            redis_client=redis_client,
        )

        # Execute downloads based on type
        results = {
            "video": None,
            "music": None,
            "cover": None,
        }

        if int(aweme_type) in (0, 4, 61):  # Video types
            if download_video:
                logger.info(f"[Celery] Downloading video: {aweme_id}")
                result = run_async(
                    DownloaderService.download_video_by_aweme_id(
                        aweme_id,
                        user_id=user_id,
                        progress_tracker=tracker
                    )
                )
                results["video"] = result.video_download_status.value if hasattr(result, 'video_download_status') else "unknown"

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
                result = run_async(
                    DownloaderService.download_images_by_aweme_id(aweme_id, user_id=user_id)
                )
                results["video"] = result.video_download_status.value if hasattr(result, 'video_download_status') else "unknown"

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

        # Mark complete in Redis
        tracker.complete()

        logger.success(f"[Celery] Download task completed: {aweme_id}")
        return {
            "status": "success",
            "aweme_id": aweme_id,
            "results": results,
        }

    except Exception as e:
        logger.error(f"[Celery] Download task failed: {aweme_id}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "aweme_id": aweme_id,
            "error": str(e),
        }


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
