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
    解析单个抖音链接的 Celery 任务

    Args:
        url: 抖音链接
        user_id: 用户 ID
        video_bool: 是否下载视频
        music_bool: 是否下载音乐
        cover_bool: 是否下载封面
        categories: 视频分类

    Returns:
        dict: 解析结果
    """
    logger.info(f"[Celery] 开始解析链接任务: {url[:50]}...")

    try:
        # 提取有效 URL
        try:
            valid_urls = Utils.extract_valid_url(url)
            valid_url = valid_urls[0]
        except ValueError as e:
            return {
                "status": "failed",
                "url": url,
                "error": f"无法提取有效链接: {str(e)}",
            }

        # 导入服务（延迟导入避免循环依赖）
        from app.services.douyin_analysis import DouyinAnalysis
        from app.services.douyin_parser import DouyinParser
        from app.services.supabase_douyin_service import SupabaseDouyinService

        # 获取视频数据
        aweme_detail = run_async(DouyinAnalysis.fetch_one_video(valid_url))

        if not aweme_detail:
            logger.warning(f"[Celery] 无法获取视频信息: {valid_url}")
            raise self.retry(
                exc=Exception("无法获取视频信息"),
                countdown=30 * (2 ** self.request.retries)
            )

        # 解析视频数据
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
            logger.warning(f"[Celery] 视频解析失败: {valid_url}")
            raise self.retry(
                exc=Exception("视频解析失败"),
                countdown=30 * (2 ** self.request.retries)
            )

        aweme_id = parsed_data.get("aweme_id")
        parsed_data["user_id"] = user_id

        # 处理存储和下载
        result = run_async(
            SupabaseDouyinService.process_video(aweme_id, parsed_data)
        )

        if result.get("success"):
            logger.success(f"[Celery] 链接解析成功: {aweme_id}")
            return {
                "status": "success",
                "url": valid_url,
                "aweme_id": aweme_id,
                "video_title": parsed_data.get("video_title"),
                "message": result.get("message"),
            }
        else:
            logger.warning(f"[Celery] 处理失败: {result.get('message')}")
            return {
                "status": "failed",
                "url": valid_url,
                "aweme_id": aweme_id,
                "error": result.get("message"),
            }

    except Exception as e:
        logger.error(f"[Celery] 解析任务异常: {url}, 错误: {str(e)}")
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
