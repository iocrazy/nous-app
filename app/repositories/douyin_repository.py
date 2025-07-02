# app/repositories/douyin_repository.py

"""
抖音视频数据仓储模块

提供抖音视频数据的增删改查操作，封装与数据库的交互逻辑。
使用异步 SQLAlchemy 实现高效的数据库操作。
"""

import json
from datetime import datetime
from typing import Dict,  Optional, Any, Union
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.utils import Utils
from app.models.douyin import Douyin
from app.repositories.base_repository import BaseRepository
from app.core.enums import DownloadStatus

from app.models.base import DBModel


class DouyinRepository(BaseRepository[Douyin]):
    """抖音视频数据仓储类"""

    def __init__(self, db: AsyncSession):
        """
        初始化仓储
        
        Args:
            db: 异步数据库会话
        """
        super().__init__(db, Douyin)

    async def check_video_existence(self, aweme_id: str) -> bool:
        """
        检查视频是否存在

        Args:
            aweme_id: 视频唯一标识

        Returns:
            bool: 视频是否存在
        """

        stmt = select(Douyin).where(Douyin.aweme_id == aweme_id)
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            return True
        else:
            return False

    async def check_video_downloaded(self, aweme_id: str) -> bool:
        """
        检查视频是否已下载

        Args:
            aweme_id: 视频唯一标识

        Returns:
            bool: 视频是否已下载
        """

        stmt = select(Douyin).where(Douyin.aweme_id == aweme_id)
        result = await self.db.execute(stmt)
        douyin = result.scalar_one_or_none()
        if not douyin:
            return False

        return douyin.video_download_status is DownloadStatus.COMPLETED and (bool(douyin.video_download_urls) or bool(douyin.image_download_urls))


    async def check_music_downloaded(self, aweme_id: str) -> bool:
        """
        检查音乐是否已下载

        Args:
            aweme_id: 视频唯一标识

        Returns:
            bool: 音乐是否已下载
        """

        stmt = select(Douyin).where(Douyin.aweme_id == aweme_id)
        result = await self.db.execute(stmt)
        douyin = result.scalar_one_or_none()
        if not douyin:
            return False

        return (douyin.music_download_status is DownloadStatus.COMPLETED) and bool(douyin.music_download_urls)




    async def mark_video_as_downloaded(self, aweme_id: str, download_path: str, download_duration: float) -> Optional[Douyin]:
        """
        标记视频为已下载状态
        
        Args:
            aweme_id: 视频唯一标识
            download_path: 视频文件的保存路径
            download_duration: 下载耗时（秒）

        Returns:
            Optional[Douyin]: 更新后的视频记录，未找到则返回 None
        """
        return await self.update(aweme_id, {
            "video_download_status": DownloadStatus.COMPLETED,
            "download_path": download_path,
            "download_duration": download_duration
        })

    async def mark_music_as_downloaded(self, aweme_id: str,music_path: str ) -> Optional[
        Douyin]:
        """
        标记视频为已下载状态

        Args:
            aweme_id: 视频唯一标识
            music_path: 视频文件的保存路径


        Returns:
            Optional[Douyin]: 更新后的视频记录，未找到则返回 None
        """
        return await self.update(aweme_id, {
            "music_download_status": DownloadStatus.COMPLETED,
            "music_path": music_path
        })

    async def update(self, aweme_id: str, data: Dict[str, Any]) -> Optional[Douyin]:
        """
        更新抖音视频记录

        Args:
            aweme_id: 视频唯一标识
            data: 要更新的数据字典

        Returns:
            Optional[Douyin]: 更新后的视频记录，未找到则返回 None
        """
        logger.debug(f"更新抖音视频: {aweme_id}")

        # 首先获取实体
        stmt = select(self.model_class).where(self.model_class.aweme_id == aweme_id)
        result = await self.db.execute(stmt)
        entity = result.scalar_one_or_none()

        if entity is None:
            logger.warning(f"未找到aweme_id为 {aweme_id} 的视频记录，无法更新")
            return None

        # 记录更新前的值
        logger.debug(f"更新前的实体: {Utils.shorten_item(entity,50)}")
        
        # 跟踪更新的字段
        updated_fields = {}
        
        # 使用对象属性方式更新，这样可以触发SQLAlchemy的事件和属性设置逻辑
        for key, value in data.items():
            if hasattr(entity, key):
                old_value = getattr(entity, key)
                if old_value != value:  # 只记录实际变化的字段
                    updated_fields[key] = {"old": Utils.shorten_item(old_value,30), "new": Utils.shorten_item(value,30)}
                    setattr(entity, key, value)
        
        # 只记录实际更新的字段
        if updated_fields:
            logger.success(f"更新的字段: {updated_fields}")
        else:
            logger.info("没有字段发生实际更新")

        return entity


    async def get_music_data(self, aweme_id: str) -> dict:
        """
        获取音乐下载信息

        Args:
            aweme_id: 视频ID

        Returns:
            dict: 包含 music_download_urls 和 music_name 的字典

        Raises:
            ValueError: 当找不到指定的视频记录时
        """
        stmt = select(Douyin).where(Douyin.aweme_id == aweme_id)
        result = await self.db.execute(stmt)
        douyin = result.scalar_one_or_none()

        if douyin.music_download_urls:
            return {
                "music_download_urls": douyin.music_download_urls,
                "music_name": douyin.music_name
            }
        else:
            raise ValueError(f"No music record found for aweme_id: {aweme_id}.")

    async def has_video_download_urls(self, aweme_id: str) -> bool:
        """
        检查视频是否有可用的下载URL

        Args:
            aweme_id: 视频唯一标识

        Returns:
            bool: 是否有可用的视频下载URL
        """
        douyin = await self.get_douyin_by_aweme_id(aweme_id)
        if not douyin:
            return False
        return bool(douyin.video_download_urls)

    async def has_music_download_urls(self, aweme_id: str) -> bool:
        """
        检查视频是否有可用的音乐下载URL

        Args:
            aweme_id: 视频唯一标识

        Returns:
            bool: 是否有可用的音乐下载URL
        """
        douyin = await self.get_douyin_by_aweme_id(aweme_id)
        if not douyin:
            return False
        return bool(douyin.music_download_urls)

    async def get_douyin_by_aweme_id(self, aweme_id: str) -> Optional[Douyin]:
        """
        通过 aweme_id 获取视频记录

        Args:
            aweme_id: 视频唯一标识

        Returns:
            Optional[Douyin]: 视频记录，未找到则返回 None
        """
        stmt = select(Douyin).where(Douyin.aweme_id == aweme_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

