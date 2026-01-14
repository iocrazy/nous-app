# backend/app/repositories/user_logs_repository.py

"""
用户日志仓库

提供用户操作日志的存储和查询功能。
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from loguru import logger

from app.db.supabase_client import SupabaseClient


class UserLogsRepository:
    """用户日志仓库"""

    TABLE_NAME = "user_logs"

    def __init__(self):
        self.client = SupabaseClient.get_admin_client()

    async def create(
        self,
        user_id: str,
        action: str,
        message: str,
        status: str = "info",
        aweme_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict]:
        """
        创建日志记录

        Args:
            user_id: 用户 ID
            action: 操作类型 (fetch, download, delete, retry, update, login, logout)
            message: 日志消息
            status: 状态 (success, error, warning, info, pending)
            aweme_id: 关联的视频 ID（可选）
            details: 额外详情（可选）

        Returns:
            创建的日志记录
        """
        try:
            data = {
                "user_id": user_id,
                "action": action,
                "message": message,
                "status": status,
            }

            if aweme_id:
                data["aweme_id"] = aweme_id
            if details:
                data["details"] = details

            result = self.client.table(self.TABLE_NAME).insert(data).execute()

            if result.data:
                logger.debug(f"日志记录创建成功: {action} - {message}")
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"创建日志记录失败: {e}")
            return None

    async def get_recent(
        self,
        user_id: str,
        limit: int = 20,
        action: Optional[str] = None
    ) -> List[Dict]:
        """
        获取最近的日志记录

        Args:
            user_id: 用户 ID
            limit: 返回数量
            action: 筛选特定操作类型（可选）

        Returns:
            日志记录列表
        """
        try:
            query = (
                self.client.table(self.TABLE_NAME)
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
            )

            if action:
                query = query.eq("action", action)

            result = query.execute()
            return result.data or []

        except Exception as e:
            logger.error(f"获取日志记录失败: {e}")
            return []

    async def get_by_aweme_id(
        self,
        user_id: str,
        aweme_id: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        获取特定视频的日志记录

        Args:
            user_id: 用户 ID
            aweme_id: 视频 ID
            limit: 返回数量

        Returns:
            日志记录列表
        """
        try:
            result = (
                self.client.table(self.TABLE_NAME)
                .select("*")
                .eq("user_id", user_id)
                .eq("aweme_id", aweme_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []

        except Exception as e:
            logger.error(f"获取视频日志失败: {e}")
            return []


# 便捷的日志记录函数
async def log_user_action(
    user_id: str,
    action: str,
    message: str,
    status: str = "info",
    aweme_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> None:
    """便捷的日志记录函数"""
    repo = UserLogsRepository()
    await repo.create(
        user_id=user_id,
        action=action,
        message=message,
        status=status,
        aweme_id=aweme_id,
        details=details
    )
