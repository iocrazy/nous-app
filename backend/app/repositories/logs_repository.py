"""Repository for User Logs data access.

ORM 2.0 migration (Batch L2): ``LogsRepository`` is the legacy supabase-py REST
implementation; ``LogsRepositoryOrm`` (in ``logs_repository_orm.py``) is the
SQLAlchemy 2.0 ORM successor. Call sites go through ``get_logs_repository()``
(bottom of this file) which picks the ORM subclass when ``USE_ORM_LOGS`` is on
AND the engine is configured.

NOTE: the ``user_logs`` table is ALSO served by ``UserLogsRepository`` (the
append-only writer + distinct reads) — disjoint method sets, both live, each
with its own ORM subclass.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.logs_repository_orm import LogsRepositoryOrm


class LogsRepository:
    """Repository for user logs CRUD operations."""

    TABLE_NAME = "user_logs"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """Get table reference."""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def get_logs(
        self,
        user_id: str,
        levels: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[List[dict], int]:
        """
        Get user logs with filtering and pagination.

        Args:
            user_id: User ID to filter logs
            levels: List of status levels to filter (success, info, warning, error, pending)
            start_date: Start date filter
            end_date: End date filter
            search: Search keyword in message field
            page: Page number (1-indexed)
            page_size: Items per page (50, 100, 200)

        Returns:
            Tuple of (logs list, total count)
        """
        table = await self._get_table()

        # Build query for data
        query = table.select("*").eq("user_id", user_id)

        # Apply level filter
        if levels:
            query = query.in_("status", levels)

        # Apply date range filter
        if start_date:
            query = query.gte("created_at", start_date.isoformat())
        if end_date:
            # Add one day to include the end date fully
            end_datetime = datetime.combine(end_date, datetime.max.time())
            query = query.lte("created_at", end_datetime.isoformat())

        # Apply search filter
        if search:
            query = query.ilike("message", f"%{search}%")

        # Get total count first
        count_query = table.select("*", count="exact").eq("user_id", user_id)
        if levels:
            count_query = count_query.in_("status", levels)
        if start_date:
            count_query = count_query.gte("created_at", start_date.isoformat())
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            count_query = count_query.lte("created_at", end_datetime.isoformat())
        if search:
            count_query = count_query.ilike("message", f"%{search}%")

        count_result = await count_query.execute()
        total = count_result.count or 0

        # Apply pagination and ordering
        offset = (page - 1) * page_size
        query = query.order("created_at", desc=True).range(
            offset, offset + page_size - 1
        )

        result = await query.execute()

        return result.data, total

    async def get_logs_for_export(
        self,
        user_id: str,
        levels: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        search: Optional[str] = None,
        limit: int = 10000,
    ) -> List[dict]:
        """
        Get all logs matching filters for export (no pagination).

        Args:
            user_id: User ID to filter logs
            levels: List of status levels to filter
            start_date: Start date filter
            end_date: End date filter
            search: Search keyword in message field
            limit: Maximum number of logs to export

        Returns:
            List of log entries
        """
        table = await self._get_table()

        query = table.select("*").eq("user_id", user_id)

        if levels:
            query = query.in_("status", levels)
        if start_date:
            query = query.gte("created_at", start_date.isoformat())
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            query = query.lte("created_at", end_datetime.isoformat())
        if search:
            query = query.ilike("message", f"%{search}%")

        query = query.order("created_at", desc=True).limit(limit)

        result = await query.execute()
        return result.data

    async def create_log(
        self,
        user_id: str,
        action: str,
        message: str,
        status: str = "info",
        aweme_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> dict:
        """
        Create a new log entry.

        Args:
            user_id: User ID
            action: Action type (fetch, download, delete, etc.)
            message: Log message
            status: Status level (success, info, warning, error, pending)
            aweme_id: Optional related video ID
            details: Optional additional details (JSON)

        Returns:
            Created log entry
        """
        table = await self._get_table()

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

        result = await table.insert(data).execute()
        logger.info(f"Created log for user {user_id}: {action} - {message}")
        return result.data[0] if result.data else None

    async def delete_logs(
        self, user_id: str, before_date: Optional[date] = None
    ) -> int:
        """
        Delete logs for a user, optionally before a specific date.

        Args:
            user_id: User ID
            before_date: Delete logs before this date (optional)

        Returns:
            Number of deleted logs
        """
        table = await self._get_table()

        query = table.delete().eq("user_id", user_id)

        if before_date:
            query = query.lt("created_at", before_date.isoformat())

        result = await query.execute()
        deleted_count = len(result.data) if result.data else 0
        logger.info(f"Deleted {deleted_count} logs for user {user_id}")
        return deleted_count


def get_logs_repository() -> Union["LogsRepository", "LogsRepositoryOrm"]:
    """Return the right LogsRepository implementation per env.

    ORM when ``USE_ORM_LOGS`` is set AND the SQLAlchemy engine is configured;
    otherwise the legacy supabase-py REST path. A flag-on but engine-missing
    deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_LOGS:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.logs_repository_orm import LogsRepositoryOrm

            return LogsRepositoryOrm()
        logger.warning(
            "USE_ORM_LOGS=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return LogsRepository()
