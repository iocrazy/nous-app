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
