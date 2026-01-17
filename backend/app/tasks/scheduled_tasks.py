# app/tasks/scheduled_tasks.py

"""
定时任务模块

包含由 Celery Beat 调度的定时任务。
"""

import asyncio
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from celery import shared_task
from loguru import logger

from app.core.config import settings
from app.core.enums import DownloadStatus


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


@shared_task
def cleanup_temp_files():
    """
    清理临时文件

    删除超过 7 天的临时文件和空目录。
    每天执行一次。
    """
    logger.info("[Celery Beat] 开始清理临时文件...")

    try:
        from app.core.utils import Utils

        # 获取下载基础路径
        try:
            base_path = Path(Utils.get_download_base_path())
        except ValueError:
            logger.warning("[Celery Beat] 未配置下载路径，跳过清理")
            return {"status": "skipped", "message": "未配置下载路径"}

        if not base_path.exists():
            return {"status": "skipped", "message": "下载目录不存在"}

        # 清理统计
        files_deleted = 0
        dirs_deleted = 0
        space_freed = 0
        cutoff_time = datetime.now() - timedelta(days=7)

        # 遍历目录清理临时文件
        for root, dirs, files in os.walk(base_path, topdown=False):
            root_path = Path(root)

            # 删除临时文件（.tmp, .part, .downloading）
            for file in files:
                file_path = root_path / file
                if file.endswith(('.tmp', '.part', '.downloading')):
                    try:
                        file_stat = file_path.stat()
                        if datetime.fromtimestamp(file_stat.st_mtime) < cutoff_time:
                            space_freed += file_stat.st_size
                            file_path.unlink()
                            files_deleted += 1
                            logger.debug(f"删除临时文件: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除文件失败 {file_path}: {e}")

            # 删除空目录
            for dir_name in dirs:
                dir_path = root_path / dir_name
                try:
                    if dir_path.is_dir() and not any(dir_path.iterdir()):
                        dir_path.rmdir()
                        dirs_deleted += 1
                        logger.debug(f"删除空目录: {dir_path}")
                except Exception as e:
                    logger.warning(f"删除目录失败 {dir_path}: {e}")

        result = {
            "status": "success",
            "files_deleted": files_deleted,
            "dirs_deleted": dirs_deleted,
            "space_freed_mb": round(space_freed / (1024 * 1024), 2),
        }

        logger.success(
            f"[Celery Beat] 清理完成: 删除 {files_deleted} 个文件, "
            f"{dirs_deleted} 个空目录, 释放 {result['space_freed_mb']} MB"
        )

        return result

    except Exception as e:
        logger.error(f"[Celery Beat] 清理临时文件失败: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def retry_failed_downloads():
    """
    重试失败的下载

    查找下载失败的记录并重新提交下载任务。
    每小时执行一次。
    """
    logger.info("[Celery Beat] 开始重试失败的下载...")

    try:
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
        from app.tasks.download_tasks import download_video_task, download_images_task

        repo = SupabaseDouyinRepository()

        # 获取失败的下载（最多 50 条）
        failed_videos = run_async(
            repo.get_by_status(DownloadStatus.FAILED, limit=50)
        )

        if not failed_videos:
            logger.info("[Celery Beat] 没有需要重试的下载")
            return {"status": "success", "retried": 0}

        retried_count = 0

        for video in failed_videos:
            aweme_id = video.get("aweme_id")
            aweme_type = video.get("aweme_type", 0)
            user_id = video.get("user_id")

            try:
                # 重置状态为 PENDING
                run_async(
                    repo.update(aweme_id, {
                        "video_download_status": DownloadStatus.PENDING.value,
                        "error_message": None
                    }, user_id=user_id)
                )

                # 根据类型提交下载任务
                if int(aweme_type) in (0, 4, 61):  # 视频类型
                    download_video_task.delay(aweme_id, user_id)
                elif int(aweme_type) in (2, 68):  # 图集类型
                    download_images_task.delay(aweme_id, user_id)

                retried_count += 1
                logger.debug(f"已重新提交下载任务: {aweme_id}")

            except Exception as e:
                logger.warning(f"重试下载失败 {aweme_id}: {e}")

        result = {
            "status": "success",
            "total_failed": len(failed_videos),
            "retried": retried_count,
        }

        logger.success(f"[Celery Beat] 重试完成: {retried_count}/{len(failed_videos)} 个任务已重新提交")

        return result

    except Exception as e:
        logger.error(f"[Celery Beat] 重试失败下载失败: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def update_statistics():
    """
    更新统计数据

    计算并缓存各种统计数据。
    每 6 小时执行一次。
    """
    logger.info("[Celery Beat] 开始更新统计数据...")

    try:
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository

        repo = SupabaseDouyinRepository()

        # 获取全局统计
        stats = run_async(repo.get_statistics())

        result = {
            "status": "success",
            "updated_at": datetime.now().isoformat(),
            "statistics": stats,
        }

        logger.success(f"[Celery Beat] 统计数据更新完成: {stats}")

        return result

    except Exception as e:
        logger.error(f"[Celery Beat] 更新统计数据失败: {e}")
        return {"status": "failed", "error": str(e)}


@shared_task
def health_check():
    """
    健康检查任务

    检查系统各组件状态。
    可用于监控告警。
    """
    logger.info("[Celery Beat] 执行健康检查...")

    checks = {
        "celery": "ok",
        "redis": "unknown",
        "supabase": "unknown",
        "storage": "unknown",
    }

    # 检查 Redis
    try:
        from app.celery_app import celery_app
        celery_app.control.ping(timeout=5)
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)[:50]}"

    # 检查 Supabase
    try:
        from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
        repo = SupabaseDouyinRepository()
        # 简单查询测试连接
        run_async(repo.get_statistics())
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {str(e)[:50]}"

    # 检查存储目录
    try:
        from app.core.utils import Utils
        base_path = Path(Utils.get_download_base_path())
        if base_path.exists() and os.access(base_path, os.W_OK):
            checks["storage"] = "ok"
        else:
            checks["storage"] = "not writable"
    except ValueError:
        checks["storage"] = "not configured"
    except Exception as e:
        checks["storage"] = f"error: {str(e)[:50]}"

    # 判断整体状态
    all_ok = all(v == "ok" for v in checks.values())
    status = "healthy" if all_ok else "degraded"

    result = {
        "status": status,
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }

    if all_ok:
        logger.success("[Celery Beat] 健康检查通过")
    else:
        logger.warning(f"[Celery Beat] 健康检查发现问题: {checks}")

    return result
