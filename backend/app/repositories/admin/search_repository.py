"""Repository for admin cross-log search + request tracing."""

from __future__ import annotations

from typing import Any

from app.db import get_async_supabase_admin


class AdminSearchRepository:
    REQUEST_LOGS_TABLE = "api_request_logs"
    APP_LOGS_TABLE = "application_logs"
    FRONTEND_LOGS_TABLE = "frontend_error_logs"
    AUDIT_LOGS_TABLE = "audit_logs"

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Time-range queries per log source ──────────────────────────

    async def request_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.REQUEST_LOGS_TABLE)
            .select(
                "id,request_id,method,path,status_code,"
                "response_time_ms,timestamp,error_detail"
            )
            .gte("timestamp", start_iso)
            .lte("timestamp", end_iso)
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def app_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.APP_LOGS_TABLE)
            .select("id,level,module,message,logged_at,extra")
            .gte("logged_at", start_iso)
            .lte("logged_at", end_iso)
            .order("logged_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def frontend_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.FRONTEND_LOGS_TABLE)
            .select("id,error_type,message,stack,url,created_at")
            .gte("created_at", start_iso)
            .lte("created_at", end_iso)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def audit_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.AUDIT_LOGS_TABLE)
            .select("id,action,target_type,target_id,admin_id,details,created_at")
            .gte("created_at", start_iso)
            .lte("created_at", end_iso)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ─── Request trace correlation ──────────────────────────────────

    async def get_request_log(self, request_id: str) -> dict[str, Any] | None:
        client = await self._client()
        result = await (
            client.table(self.REQUEST_LOGS_TABLE)
            .select("*")
            .eq("request_id", request_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    async def app_logs_by_request_id(
        self, request_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.APP_LOGS_TABLE)
            .select("level,module,message,logged_at,extra")
            .filter("extra->>request_id", "eq", request_id)
            .order("logged_at", desc=False)
            .limit(limit)
            .execute()
        )
        return result.data or []


def get_admin_search_repository() -> "AdminSearchRepository":
    """Return the right AdminSearchRepository implementation per env.

    ORM when ``USE_ORM_ADMIN_SEARCH`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_ADMIN_SEARCH:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.admin.search_repository_orm import (
                AdminSearchRepositoryOrm,
            )

            return AdminSearchRepositoryOrm()
        from loguru import logger

        logger.warning(
            "USE_ORM_ADMIN_SEARCH=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return AdminSearchRepository()
