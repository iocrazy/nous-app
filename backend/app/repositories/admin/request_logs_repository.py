"""Repositories for the three log tables surfaced in the admin console:
api_request_logs, frontend_error_logs, application_logs.

The admin router previously built each of these filter chains inline,
which made adding a new filter mean touching three places. Centralizing
them here keeps the column lists, noise-exclusion policies, and status-
group bucketing in one module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db import get_async_supabase_admin


class RequestLogsRepository:
    TABLE = "api_request_logs"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        method: Optional[str] = None,
        path: Optional[str] = None,
        status_group: Optional[str] = None,
        user_id: Optional[str] = None,
        min_response_time: Optional[int] = None,
        request_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TABLE).select("*", count="exact")

        if method:
            query = query.eq("method", method.upper())
        if path:
            query = query.ilike("path", f"%{path}%")
        if status_group == "2xx":
            query = query.gte("status_code", 200).lt("status_code", 300)
        elif status_group == "4xx":
            query = query.gte("status_code", 400).lt("status_code", 500)
        elif status_group == "5xx":
            query = query.gte("status_code", 500).lt("status_code", 600)
        if user_id:
            query = query.eq("user_id", user_id)
        if min_response_time:
            query = query.gte("response_time_ms", min_response_time)
        if request_id:
            query = query.eq("request_id", request_id)
        if start_date:
            query = query.gte("timestamp", start_date.isoformat())
        if end_date:
            query = query.lte("timestamp", end_date.isoformat())

        query = query.order("timestamp", desc=True)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0

    async def stats_since(self, start_time: datetime) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("method, status_code, path, response_time_ms, timestamp")
            .gte("timestamp", start_time.isoformat())
            .execute()
        )
        return result.data or []


class FrontendErrorLogsRepository:
    TABLE = "frontend_error_logs"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        error_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TABLE).select("*", count="exact")

        if error_type:
            query = query.eq("error_type", error_type)
        if start_date:
            query = query.gte("created_at", start_date.isoformat())
        if end_date:
            query = query.lte("created_at", end_date.isoformat())

        query = query.order("created_at", desc=True)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0


class AppLogsRepository:
    TABLE = "application_logs"

    # Infrastructure noise — hidden from the Application Logs view unless the
    # admin explicitly filters by module.
    NOISE_MODULES = ("httpx", "uvicorn.access", "uvicorn.error", "celery.beat")

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        level: Optional[str] = None,
        module: Optional[str] = None,
        message: Optional[str] = None,
        has_exception: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TABLE).select("*", count="exact")

        if not module:
            for noise in self.NOISE_MODULES:
                query = query.neq("module", noise)

        if level:
            query = query.eq("level", level.upper())
        if module:
            query = query.ilike("module", f"%{module}%")
        if message:
            query = query.ilike("message", f"%{message}%")
        if has_exception is True:
            query = query.neq("exception", None)
        elif has_exception is False:
            query = query.is_("exception", "null")
        if start_date:
            query = query.gte("logged_at", start_date.isoformat())
        if end_date:
            query = query.lte("logged_at", end_date.isoformat())

        query = query.order("logged_at", desc=True)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0
