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

    async def monitoring_stats(
        self,
        start: datetime,
        end: datetime,
        bucket_minutes: int,
    ) -> dict:
        """Server-side aggregated monitoring dashboard stats
        (``rpc_monitoring_stats``, migration 271).

        Replaces fetching ALL api_request_logs (capped 10000) + application_logs
        (capped 5000) for the window and computing 7 stat sections in Python —
        an undercount on the capped REST path and a heavy full-window fetch on
        the ORM path. The RPC computes everything (overview / trend / top-slow /
        error endpoints / level distribution / error modules / recent errors) in
        SQL over the full window and returns only the aggregated payload. Shared
        by both REST and ORM repos (the ORM subclass inherits this).

        ``bucket_minutes`` controls the request-trend bucket size and key format
        (>=1440 per-day, >=60 hour-aligned, else minute-aligned) — kept identical
        to the old ``_bucket_key`` so the dashboard chart x-axis is unchanged.
        """
        client = await self._client()
        result = await client.rpc(
            "rpc_monitoring_stats",
            {
                "p_start": start.isoformat(),
                "p_end": end.isoformat(),
                "p_bucket_minutes": bucket_minutes,
            },
        ).execute()
        return result.data or {}


def get_monitoring_repository() -> "MonitoringRepository":
    """Return the right MonitoringRepository implementation per env.

    ORM when ``USE_ORM_ADMIN_MONITORING`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings
    from app.db.engine import is_configured

    if settings.USE_ORM_ADMIN_MONITORING:
        if is_configured():
            from app.repositories.admin.monitoring_repository_orm import (
                MonitoringRepositoryOrm,
            )

            return MonitoringRepositoryOrm()
        from loguru import logger

        logger.warning(
            "USE_ORM_ADMIN_MONITORING=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )

    from app.db.shadow_compare import shadow_enabled

    if shadow_enabled("admin_monitoring") and is_configured():
        from app.db.shadow_compare import ShadowRepo
        from app.repositories.admin.monitoring_repository_orm import (
            MonitoringRepositoryOrm,
        )

        return ShadowRepo(
            MonitoringRepository(), MonitoringRepositoryOrm(), "admin_monitoring"
        )

    return MonitoringRepository()
