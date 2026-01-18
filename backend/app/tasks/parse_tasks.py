# app/tasks/parse_tasks.py

"""
解析任务模块

包含链接解析相关的 Celery 任务。
"""

import asyncio
from celery import shared_task, group
from loguru import logger

from app.core.utils import Utils


def run_async(coro):
    """在同步环境中运行异步协程"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def log_user_action(user_id: str, action: str, message: str, status: str, aweme_id: str = None):
    """Log user action helper."""
    try:
        from app.repositories.user_action_log_repository import UserActionLogRepository
        repo = UserActionLogRepository()
        await repo.log_action(
            user_id=user_id,
            action=action,
            message=message,
            status=status,
            aweme_id=aweme_id
        )
    except Exception as e:
        logger.warning(f"Failed to log user action: {e}")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def parse_single_link_task(
    self,
    url: str,
    user_id: str,
    video_bool: bool = True,
    music_bool: bool = False,
    cover_bool: bool = True,
    categories: str = None
):
    """
    Parse metadata for a Douyin link (Phase 1).

    This task quickly parses video metadata and returns it immediately.
    Downloads are handled by a separate download_media_task (Phase 2).

    Args:
        url: Douyin URL
        user_id: User ID
        video_bool: Whether to download video
        music_bool: Whether to download music
        cover_bool: Whether to download cover
        categories: Video categories

    Returns:
        dict: Metadata + download_task_id for progress tracking
    """
    logger.info(f"[Celery] Starting metadata parse: {url[:50]}...")

    try:
        # Extract valid URL
        try:
            valid_urls = Utils.extract_valid_url(url)
            valid_url = valid_urls[0]
        except ValueError as e:
            return {
                "status": "failed",
                "url": url,
                "error": f"Invalid URL: {str(e)}",
            }

        from app.services.douyin_analysis import DouyinAnalysis
        from app.services.douyin_parser import DouyinParser
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
        from app.tasks.download_tasks import download_media_task

        # Fetch video data from Douyin
        aweme_detail = run_async(DouyinAnalysis.fetch_one_video(valid_url))

        if not aweme_detail:
            logger.warning(f"[Celery] Cannot fetch video info: {valid_url}")
            raise self.retry(
                exc=Exception("Cannot fetch video info"),
                countdown=30 * (2 ** self.request.retries)
            )

        # Parse metadata (without downloading files)
        parsed_data = run_async(
            DouyinParser.parse_aweme_detail(
                aweme_detail=aweme_detail,
                valid_url=valid_url,
                download_video=video_bool,
                download_music=music_bool,
                download_cover=cover_bool,
                categories=categories
            )
        )

        if not parsed_data:
            logger.warning(f"[Celery] Parse failed: {valid_url}")
            raise self.retry(
                exc=Exception("Parse failed"),
                countdown=30 * (2 ** self.request.retries)
            )

        aweme_id = parsed_data.get("aweme_id")
        aweme_type = parsed_data.get("aweme_type", 0)
        video_title = parsed_data.get("video_title", "undefined")
        parsed_data["user_id"] = user_id

        # Save metadata to database (without downloading)
        from app.schemas.douyin import DouyinCreate
        from app.core.enums import DownloadStatus

        repo = SupabaseDouyinRepository()

        # Check if exists
        existing = run_async(repo.get_by_aweme_id(aweme_id, user_id=user_id))

        # Prepare data
        try:
            douyin_data = DouyinCreate(**parsed_data)
            data_dict = douyin_data.model_dump()
        except Exception as e:
            logger.error(f"Data validation failed: {str(e)}")
            return {
                "status": "failed",
                "url": valid_url,
                "error": f"Data validation failed: {str(e)}",
            }

        # Set download status to pending
        if video_bool:
            data_dict["video_download_status"] = DownloadStatus.PENDING.value
        else:
            data_dict["video_download_status"] = DownloadStatus.SKIPPED.value

        if music_bool:
            data_dict["music_download_status"] = DownloadStatus.PENDING.value
        else:
            data_dict["music_download_status"] = DownloadStatus.SKIPPED.value

        # Save or update
        if existing:
            run_async(repo.update(aweme_id, data_dict))
            logger.info(f"[Celery] Updated metadata: {aweme_id}")
        else:
            run_async(repo.create(data_dict))
            logger.info(f"[Celery] Created metadata: {aweme_id}")

        # Determine what needs downloading
        need_download = video_bool or music_bool or cover_bool
        download_task_id = None

        if need_download:
            # Trigger download task (Phase 2)
            download_task = download_media_task.delay(
                aweme_id=aweme_id,
                user_id=user_id,
                download_video=video_bool,
                download_music=music_bool,
                download_cover=cover_bool,
                aweme_type=aweme_type,
                video_title=video_title,
            )
            download_task_id = download_task.id
            logger.info(f"[Celery] Download task triggered: {download_task_id}")

        # Log user action
        run_async(log_user_action(
            user_id=user_id,
            action="fetch",
            message=f"{video_title[:20]}...: Metadata parsed",
            status="success",
            aweme_id=aweme_id
        ))

        # Build metadata response
        metadata = {
            "aweme_id": aweme_id,
            "video_title": parsed_data.get("video_title"),
            "author": parsed_data.get("author"),
            "author_avatar": parsed_data.get("author_avatar"),
            "duration": parsed_data.get("duration"),
            "aweme_type": aweme_type,
            "create_time": parsed_data.get("create_time"),
            "statistics": {
                "likes": parsed_data.get("likes", 0),
                "comments": parsed_data.get("comments", 0),
                "shares": parsed_data.get("shares", 0),
                "collects": parsed_data.get("collects", 0),
            },
            "cover_urls": parsed_data.get("cover_urls", []),
            "description": parsed_data.get("description"),
        }

        logger.success(f"[Celery] Metadata parse complete: {aweme_id}")

        return {
            "status": "success",
            "url": valid_url,
            "aweme_id": aweme_id,
            "download_task_id": download_task_id,
            "metadata": metadata,
        }

    except Exception as e:
        logger.error(f"[Celery] Parse task error: {url}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "url": url,
            "error": str(e),
        }


@shared_task(bind=True)
def parse_batch_links_task(
    self,
    urls: list,
    user_id: str,
    video_bool: bool = True,
    music_bool: bool = False,
    cover_bool: bool = True,
    categories: str = None
):
    """
    批量解析抖音链接的 Celery 任务

    将批量任务拆分为多个单独的子任务并行执行。

    Args:
        urls: 抖音链接列表
        user_id: 用户 ID
        video_bool: 是否下载视频
        music_bool: 是否下载音乐
        cover_bool: 是否下载封面
        categories: 视频分类

    Returns:
        dict: 批量任务结果
    """
    logger.info(f"[Celery] 开始批量解析任务: {len(urls)} 个链接")

    # 创建子任务组
    tasks = group(
        parse_single_link_task.s(
            url=url,
            user_id=user_id,
            video_bool=video_bool,
            music_bool=music_bool,
            cover_bool=cover_bool,
            categories=categories
        )
        for url in urls
    )

    # 执行任务组并等待结果
    result = tasks.apply_async()

    # 返回任务组 ID，让调用者可以追踪
    return {
        "status": "submitted",
        "total": len(urls),
        "group_id": result.id,
        "message": f"已提交 {len(urls)} 个解析任务",
    }
