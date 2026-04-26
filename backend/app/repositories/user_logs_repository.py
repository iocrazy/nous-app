# backend/app/repositories/user_logs_repository.py

"""
用户日志仓库

提供用户操作日志的存储和查询功能。
使用异步 Supabase 客户端。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class UserLogsRepository:
    """用户日志仓库 (异步)"""

    TABLE_NAME = "user_logs"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def create(
        self,
        user_id: str,
        action: str,
        message: str,
        status: str = "info",
        aweme_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
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

        Skips writes when ``user_id`` is missing — Celery retry paths
        (scheduled_tasks.retry_failed_downloads) can pull rows with a
        NULL ``user_id`` from legacy/system-initiated downloads, and
        calling create(None, ...) used to fail loudly with 23502 NOT
        NULL violation, spamming ERROR logs once per orphan download.
        Soft-skip is correct: if there's no user, there's no per-user
        log to create.
        """
        if not user_id or str(user_id).lower() in ("none", "null"):
            logger.debug(
                f"[user-logs] skipping create with missing user_id "
                f"(action={action}, status={status})"
            )
            return None
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

            table = await self._get_table()
            result = await table.insert(data).execute()

            if result.data:
                logger.debug(f"日志记录创建成功: {action} - {message}")
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"创建日志记录失败: {e}")
            return None

    async def get_recent(
        self, user_id: str, limit: int = 20, action: Optional[str] = None
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
            table = await self._get_table()
            query = (
                table.select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
            )

            if action:
                query = query.eq("action", action)

            result = await query.execute()
            return result.data or []

        except Exception as e:
            logger.error(f"获取日志记录失败: {e}")
            return []

    async def get_paginated(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 50,
        level: Optional[str] = None,
        date_range: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get paginated and filtered logs.

        Returns:
            Dict with 'logs', 'total', 'page', 'page_size', 'total_pages'
        """
        from datetime import datetime, timedelta

        try:
            table = await self._get_table()

            # Count query
            count_query = table.select("id", count="exact").eq("user_id", user_id)
            # Data query
            data_query = (
                table.select("*").eq("user_id", user_id).order("created_at", desc=True)
            )

            # Filter by status/level
            if level and level != "all":
                count_query = count_query.eq("status", level)
                data_query = data_query.eq("status", level)

            # Filter by date range
            date_from = None
            if date_range and date_range != "custom":
                days_map = {"24h": 1, "7days": 7, "30days": 30, "90days": 90}
                days = days_map.get(date_range)
                if days:
                    date_from = (datetime.utcnow() - timedelta(days=days)).isoformat()
            elif start_date:
                date_from = start_date

            if date_from:
                count_query = count_query.gte("created_at", date_from)
                data_query = data_query.gte("created_at", date_from)

            if end_date:
                count_query = count_query.lte("created_at", end_date)
                data_query = data_query.lte("created_at", end_date)

            # Search filter
            if search:
                count_query = count_query.ilike("message", f"%{search}%")
                data_query = data_query.ilike("message", f"%{search}%")

            # Execute count
            count_result = await count_query.execute()
            total = count_result.count if count_result.count is not None else 0

            # Pagination
            offset = (page - 1) * page_size
            data_query = data_query.range(offset, offset + page_size - 1)

            result = await data_query.execute()
            logs = result.data or []
            total_pages = max(1, (total + page_size - 1) // page_size)

            return {
                "logs": logs,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            }

        except Exception as e:
            logger.error(f"获取分页日志失败: {e}")
            return {
                "logs": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
            }

    async def get_by_aweme_id(
        self, user_id: str, aweme_id: str, limit: int = 10
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
            table = await self._get_table()
            result = await (
                table.select("*")
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
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """便捷的日志记录函数"""
    repo = UserLogsRepository()
    await repo.create(
        user_id=user_id,
        action=action,
        message=message,
        status=status,
        aweme_id=aweme_id,
        details=details,
    )
