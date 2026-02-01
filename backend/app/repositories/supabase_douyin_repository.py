# app/repositories/supabase_douyin_repository.py

"""
Supabase 抖音数据仓储

基于 Supabase 的抖音视频数据访问层，提供 CRUD 操作和查询功能。
使用异步 Supabase 客户端。
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.core.enums import DownloadStatus


class SupabaseDouyinRepository:
    """Supabase 抖音数据仓储 (异步)"""

    TABLE_NAME = "douyin_videos"

    def __init__(self):
        self._client = None  # 延迟初始化

    async def _get_client(self):
        """获取异步客户端"""
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建新的视频记录

        Args:
            data: 视频数据字典

        Returns:
            创建的记录
        """
        try:
            # 处理枚举类型
            if "video_download_status" in data and isinstance(data["video_download_status"], DownloadStatus):
                data["video_download_status"] = data["video_download_status"].value
            if "music_download_status" in data and isinstance(data["music_download_status"], DownloadStatus):
                data["music_download_status"] = data["music_download_status"].value
            if "cover_download_status" in data and isinstance(data["cover_download_status"], DownloadStatus):
                data["cover_download_status"] = data["cover_download_status"].value

            # 处理 datetime
            if "video_created_time" in data and isinstance(data["video_created_time"], datetime):
                data["video_created_time"] = data["video_created_time"].isoformat()
            if "download_time" in data and isinstance(data["download_time"], datetime):
                data["download_time"] = data["download_time"].isoformat()

            table = await self._get_table()
            result = await table.insert(data).execute()
            logger.info(f"创建视频记录成功: {data.get('aweme_id')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"创建视频记录失败: {e}")
            raise

    async def get_by_aweme_id(
        self,
        aweme_id: str,
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        根据 aweme_id 获取视频记录

        Args:
            aweme_id: 视频唯一标识
            user_id: 用户 ID（如果提供则只返回该用户的视频）

        Returns:
            视频记录或 None
        """
        try:
            client = await self._get_client()
            # Use videos_with_tags view which includes tags array
            query = client.table("videos_with_tags").select("*").eq("aweme_id", aweme_id)
            if user_id:
                query = query.eq("user_id", user_id)
            result = await query.execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"获取视频记录失败: {e}")
            return None

    async def update(
        self,
        aweme_id: str,
        data: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        更新视频记录

        Args:
            aweme_id: 视频唯一标识
            data: 更新数据
            user_id: 用户 ID（如果提供则只更新该用户的视频）

        Returns:
            更新后的记录
        """
        try:
            # 处理枚举类型
            if "video_download_status" in data and isinstance(data["video_download_status"], DownloadStatus):
                data["video_download_status"] = data["video_download_status"].value
            if "music_download_status" in data and isinstance(data["music_download_status"], DownloadStatus):
                data["music_download_status"] = data["music_download_status"].value
            if "cover_download_status" in data and isinstance(data["cover_download_status"], DownloadStatus):
                data["cover_download_status"] = data["cover_download_status"].value

            # 处理 datetime
            if "video_created_time" in data and isinstance(data["video_created_time"], datetime):
                data["video_created_time"] = data["video_created_time"].isoformat()
            if "download_time" in data and isinstance(data["download_time"], datetime):
                data["download_time"] = data["download_time"].isoformat()

            # 添加更新时间
            data["updated_at"] = datetime.now().isoformat()

            table = await self._get_table()
            query = table.update(data).eq("aweme_id", aweme_id)
            if user_id:
                query = query.eq("user_id", user_id)
            result = await query.execute()
            logger.info(f"更新视频记录成功: {aweme_id}")
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"更新视频记录失败: {e}")
            raise

    async def delete(self, aweme_id: str, user_id: Optional[str] = None) -> bool:
        """
        删除视频记录

        Args:
            aweme_id: 视频唯一标识
            user_id: 用户 ID（如果提供则只删除该用户的视频）

        Returns:
            是否删除成功
        """
        try:
            table = await self._get_table()
            query = table.delete().eq("aweme_id", aweme_id)
            if user_id:
                query = query.eq("user_id", user_id)
            await query.execute()
            logger.info(f"删除视频记录成功: {aweme_id}")
            return True
        except Exception as e:
            logger.error(f"删除视频记录失败: {e}")
            return False

    async def check_video_existence(self, aweme_id: str) -> bool:
        """检查视频是否存在"""
        result = await self.get_by_aweme_id(aweme_id)
        return result is not None

    async def check_video_downloaded(self, aweme_id: str) -> bool:
        """检查视频是否已下载"""
        result = await self.get_by_aweme_id(aweme_id)
        if result:
            return result.get("video_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def check_music_downloaded(self, aweme_id: str) -> bool:
        """检查音乐是否已下载"""
        result = await self.get_by_aweme_id(aweme_id)
        if result:
            return result.get("music_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def check_cover_downloaded(self, aweme_id: str) -> bool:
        """检查封面是否已下载"""
        result = await self.get_by_aweme_id(aweme_id)
        if result:
            return result.get("cover_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def mark_video_as_downloaded(
        self,
        aweme_id: str,
        download_path: str,
        duration: float,
        storage_size: int = 0
    ) -> Optional[Dict[str, Any]]:
        """标记视频为已下载"""
        data = {
            "video_download_status": DownloadStatus.COMPLETED.value,
            "download_path": download_path,
            "download_duration": duration,
            "download_time": datetime.now().isoformat()
        }
        if storage_size > 0:
            data["storage_size"] = storage_size
        return await self.update(aweme_id, data)

    async def mark_music_as_downloaded(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """标记音乐为已下载"""
        return await self.update(aweme_id, {
            "music_download_status": DownloadStatus.COMPLETED.value
        })

    async def mark_images_as_downloaded(
        self,
        aweme_id: str,
        download_path: str,
        duration: float
    ) -> Optional[Dict[str, Any]]:
        """标记图片为已下载"""
        return await self.update(aweme_id, {
            "video_download_status": DownloadStatus.COMPLETED.value,
            "download_path": download_path,
            "download_duration": duration,
            "download_time": datetime.now().isoformat()
        })

    async def get_music_data(self, aweme_id: str) -> Dict[str, Any]:
        """
        获取音乐下载所需数据

        Args:
            aweme_id: 视频唯一标识

        Returns:
            包含音乐URL和名称的字典

        Raises:
            ValueError: 如果找不到视频记录
        """
        result = await self.get_by_aweme_id(aweme_id)
        if not result:
            raise ValueError(f"找不到视频数据: {aweme_id}")
        return {
            "music_download_urls": result.get("music_download_urls"),
            "music_name": result.get("music_name")
        }

    async def mark_download_failed(
        self,
        aweme_id: str,
        error_message: str,
        is_video: bool = True
    ) -> Optional[Dict[str, Any]]:
        """标记下载失败"""
        status_field = "video_download_status" if is_video else "music_download_status"
        return await self.update(aweme_id, {
            status_field: DownloadStatus.FAILED.value,
            "error_message": error_message
        })

    async def get_pending_downloads(
        self,
        status: DownloadStatus = DownloadStatus.PENDING,
        limit: int = 100,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """获取待下载的视频列表"""
        try:
            table = await self._get_table()
            query = table.select("*").eq("video_download_status", status.value)
            if user_id:
                query = query.eq("user_id", user_id)
            result = await query.limit(limit).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"获取待下载列表失败: {e}")
            return []

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        order_by: str = "created_at",
        ascending: bool = False,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取所有视频记录（分页）

        Args:
            skip: 跳过记录数
            limit: 返回记录数
            order_by: 排序字段
            ascending: 是否升序
            user_id: 用户 ID（如果提供则只返回该用户的视频）

        Returns:
            视频记录列表（包含 tags 数组）
        """
        try:
            client = await self._get_client()
            # Use videos_with_tags view which includes tags array
            query = client.table("videos_with_tags").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            query = query.order(order_by, desc=not ascending)
            query = query.range(skip, skip + limit - 1)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"获取视频列表失败: {e}")
            return []

    async def search(
        self,
        keyword: Optional[str] = None,
        author: Optional[str] = None,
        status: Optional[DownloadStatus] = None,
        aweme_type: Optional[str] = None,
        category: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        搜索视频记录

        Args:
            keyword: 关键词（搜索标题和描述）
            author: 作者
            status: 下载状态
            aweme_type: 媒体类型
            category: 分类
            start_date: 开始日期
            end_date: 结束日期
            skip: 跳过记录数
            limit: 返回记录数
            user_id: 用户 ID（如果提供则只搜索该用户的视频）

        Returns:
            匹配的视频记录列表
        """
        try:
            table = await self._get_table()
            query = table.select("*")

            if user_id:
                query = query.eq("user_id", user_id)

            if keyword:
                query = query.or_(f"video_title.ilike.%{keyword}%,video_desc.ilike.%{keyword}%")

            if author:
                query = query.ilike("author", f"%{author}%")

            if status:
                query = query.eq("video_download_status", status.value)

            if aweme_type:
                query = query.eq("aweme_type", aweme_type)

            # TODO: category search needs to be reimplemented via video_tags table
            # if category:
            #     query = query.ilike("video_categories", f"%{category}%")

            if start_date:
                query = query.gte("video_created_time", start_date.isoformat())

            if end_date:
                query = query.lte("video_created_time", end_date.isoformat())

            query = query.order("created_at", desc=True).range(skip, skip + limit - 1)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"搜索视频失败: {e}")
            return []

    async def get_statistics(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        获取统计信息

        Args:
            user_id: 用户 ID（如果提供则只统计该用户的视频）
        """
        try:
            table = await self._get_table()

            # 总数
            total_query = table.select("*", count="exact")
            if user_id:
                total_query = total_query.eq("user_id", user_id)
            total_result = await total_query.execute()
            total = total_result.count or 0

            # 各状态数量
            pending_query = table.select("*", count="exact").eq(
                "video_download_status", DownloadStatus.PENDING.value
            )
            if user_id:
                pending_query = pending_query.eq("user_id", user_id)
            pending_result = await pending_query.execute()
            pending = pending_result.count or 0

            completed_query = table.select("*", count="exact").eq(
                "video_download_status", DownloadStatus.COMPLETED.value
            )
            if user_id:
                completed_query = completed_query.eq("user_id", user_id)
            completed_result = await completed_query.execute()
            completed = completed_result.count or 0

            failed_query = table.select("*", count="exact").eq(
                "video_download_status", DownloadStatus.FAILED.value
            )
            if user_id:
                failed_query = failed_query.eq("user_id", user_id)
            failed_result = await failed_query.execute()
            failed = failed_result.count or 0

            # Calculate total storage bytes
            storage_query = table.select("video_datasize_bytes")
            if user_id:
                storage_query = storage_query.eq("user_id", user_id)
            storage_result = await storage_query.execute()
            total_storage_bytes = sum(
                (row.get("video_datasize_bytes") or 0) for row in (storage_result.data or [])
            )

            # Count unique authors
            authors_query = table.select("author")
            if user_id:
                authors_query = authors_query.eq("user_id", user_id)
            authors_result = await authors_query.execute()
            unique_authors = len(set(
                row.get("author") for row in (authors_result.data or []) if row.get("author")
            ))

            return {
                "total": total,
                "pending": pending,
                "completed": completed,
                "failed": failed,
                "skipped": total - pending - completed - failed,
                "total_storage_bytes": total_storage_bytes,
                "unique_authors": unique_authors
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {
                "total": 0, "pending": 0, "completed": 0, "failed": 0, "skipped": 0,
                "total_storage_bytes": 0, "unique_authors": 0
            }
