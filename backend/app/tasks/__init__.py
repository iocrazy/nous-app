# app/tasks/__init__.py

"""
Celery 任务模块

包含所有异步任务定义：
- download_tasks: 下载相关任务
- parse_tasks: 解析相关任务
- scheduled_tasks: 定时任务
"""

from app.tasks.download_tasks import (
    download_video_task,
    download_images_task,
    download_music_task,
    download_cover_task,
)
from app.tasks.parse_tasks import (
    parse_single_link_task,
    parse_batch_links_task,
)
from app.tasks.scheduled_tasks import (
    cleanup_temp_files,
    retry_failed_downloads,
    update_statistics,
)

__all__ = [
    # 下载任务
    "download_video_task",
    "download_images_task",
    "download_music_task",
    "download_cover_task",
    # 解析任务
    "parse_single_link_task",
    "parse_batch_links_task",
    # 定时任务
    "cleanup_temp_files",
    "retry_failed_downloads",
    "update_statistics",
]
