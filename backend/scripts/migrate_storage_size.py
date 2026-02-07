#!/usr/bin/env python3
"""
迁移脚本：计算并更新已有视频的 storage_size

用法:
    cd backend
    uv run python scripts/migrate_storage_size.py
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.supabase_client import get_async_supabase_admin
from app.core.config import settings
from loguru import logger


async def migrate_storage_sizes():
    """计算并更新所有视频的 storage_size"""

    logger.info("开始迁移 storage_size...")

    client = await get_async_supabase_admin()
    base_path = settings.DOWNLOAD_PATH

    # 获取所有有 download_path 但没有 storage_size 的视频
    result = await client.table("videos").select(
        "id, platform_id, download_path, storage_size"
    ).not_.is_("download_path", "null").execute()

    videos = result.data
    logger.info(f"找到 {len(videos)} 个已下载的视频")

    updated = 0
    skipped = 0
    not_found = 0

    for video in videos:
        video_id = video["id"]
        platform_id = video["platform_id"]
        download_path = video["download_path"]
        current_size = video.get("storage_size") or 0

        # 如果已有大小，跳过
        if current_size > 0:
            skipped += 1
            continue

        # 构建完整路径
        if download_path.startswith("/"):
            full_path = download_path
        else:
            full_path = os.path.join(base_path, download_path)

        # 计算文件大小
        if os.path.exists(full_path):
            file_size = os.path.getsize(full_path)

            # 更新数据库
            try:
                await client.table("videos").update({
                    "storage_size": file_size
                }).eq("id", video_id).execute()

                updated += 1
                logger.debug(f"更新 {platform_id}: {file_size / 1024 / 1024:.2f} MB")
            except Exception as e:
                logger.error(f"更新 {platform_id} 失败: {e}")
        else:
            not_found += 1
            logger.warning(f"文件不存在: {full_path}")

    logger.info(f"""
迁移完成:
  - 更新: {updated}
  - 跳过(已有大小): {skipped}
  - 文件不存在: {not_found}
  - 总计: {len(videos)}
""")


if __name__ == "__main__":
    asyncio.run(migrate_storage_sizes())
