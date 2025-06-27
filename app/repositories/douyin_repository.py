# app/repositories/douyin_repository.py

"""
抖音视频数据仓储模块

提供抖音视频数据的增删改查操作，封装与数据库的交互逻辑。
使用异步 SQLAlchemy 实现高效的数据库操作。
"""

from typing import List, Optional, Dict, Any
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import or_, and_

from app.models.duyin import Douyin


class DouyinRepository:
    """抖音视频数据仓储类"""
    
    def __init__(self, db: AsyncSession):
        """
        初始化仓储
        
        Args:
            db: 异步数据库会话
        """
        self.db = db
    
    async def create(self, data: Dict[str, Any]) -> Douyin:
        """
        创建新的抖音视频记录
        
        Args:
            data: 视频数据字典
            
        Returns:
            Douyin: 创建的视频记录
        """
        douyin = Douyin(**data)
        self.db.add(douyin)
        await self.db.commit()
        await self.db.refresh(douyin)
        return douyin
    
    async def get_by_id(self, aweme_id: str) -> Optional[Douyin]:
        """
        通过 aweme_id 获取视频记录
        
        Args:
            aweme_id: 视频唯一标识
            
        Returns:
            Optional[Douyin]: 找到的视频记录，未找到则返回 None
        """
        query = select(Douyin).where(Douyin.aweme_id == aweme_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def get_all(self, skip: int = 0, limit: int = 100) -> List[Douyin]:
        """
        获取所有视频记录，支持分页
        
        Args:
            skip: 跳过的记录数
            limit: 返回的最大记录数
            
        Returns:
            List[Douyin]: 视频记录列表
        """
        query = select(Douyin).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def update(self, aweme_id: str, data: Dict[str, Any]) -> Optional[Douyin]:
        """
        更新视频记录
        
        Args:
            aweme_id: 视频唯一标识
            data: 要更新的数据字典
            
        Returns:
            Optional[Douyin]: 更新后的视频记录，未找到则返回 None
        """
        douyin = await self.get_by_id(aweme_id)
        if not douyin:
            return None
            
        for key, value in data.items():
            if hasattr(douyin, key):
                setattr(douyin, key, value)
                
        await self.db.commit()
        await self.db.refresh(douyin)
        return douyin
    
    async def delete(self, aweme_id: str) -> bool:
        """
        删除视频记录
        
        Args:
            aweme_id: 视频唯一标识
            
        Returns:
            bool: 删除成功返回 True，未找到记录返回 False
        """
        douyin = await self.get_by_id(aweme_id)
        if not douyin:
            return False
            
        await self.db.delete(douyin)
        await self.db.commit()
        return True
    
    async def search(self, keyword: str) -> List[Douyin]:
        """
        搜索视频记录
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            List[Douyin]: 匹配的视频记录列表
        """
        query = select(Douyin).where(
            or_(
                Douyin.title.contains(keyword),
                Douyin.author.contains(keyword)
            )
        )
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def mark_as_downloaded(self, aweme_id: str) -> Optional[Douyin]:
        """
        标记视频为已下载状态
        
        Args:
            aweme_id: 视频唯一标识
            
        Returns:
            Optional[Douyin]: 更新后的视频记录，未找到则返回 None
        """
        from datetime import datetime
        
        return await self.update(aweme_id, {
            "is_downloaded": True,
            "download_time": datetime.now()
        })