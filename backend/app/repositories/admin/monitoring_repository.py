"""Repository for admin monitoring dashboard — reads request/app/fe logs.

The monitoring router ran three hand-rolled Supabase queries against
api_request_logs / application_logs / frontend_error_logs; centralizing
them here keeps the log-table names and the column list in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_async_supabase_admin


class MonitoringRepository:
    REQUEST_LOGS_TABLE = "api_request_logs"
    APP_LOGS_TABLE = "application_logs"
    FE_ERRORS_TABLE = "frontend_error_logs"

    REQUEST_COLUMNS = "path,method,status_code,response_time_ms,timestamp"
    APP_COLUMNS = "level,module,message,logged_at"

    async def _client(self):
        return await get_async_supabase_admin()

    async def request_logs_between(
        self,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.REQUEST_LOGS_TABLE)
            .select(self.REQUEST_COLUMNS)
            .gte("timestamp", start.isoformat())
            .lte("timestamp", end.isoformat())
            .order("timestamp", desc=False)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def app_logs_between(
        self,
        start: datetime,
        end: datetime,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.APP_LOGS_TABLE)
            .select(self.APP_COLUMNS)
            .gte("logged_at", start.isoformat())
            .lte("logged_at", end.isoformat())
            .order("logged_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def frontend_error_count(
        self,
        start: datetime,
        end: datetime,
    ) -> int:
        client = await self._client()
        result = await (
            client.table(self.FE_ERRORS_TABLE)
            .select("id", count="exact")
            .gte("created_at", start.isoformat())
            .lte("created_at", end.isoformat())
            .execute()
        )
        return result.count or 0
