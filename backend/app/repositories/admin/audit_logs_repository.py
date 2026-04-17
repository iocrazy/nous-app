"""Repository for audit_logs (admin activity trail)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db import get_async_supabase_admin


class AuditLogsRepository:
    TABLE = "audit_logs"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        admin_id: Optional[str] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (rows, total_count). Total is exact-count from PostgREST."""
        client = await self._client()
        query = client.table(self.TABLE).select("*", count="exact")

        if admin_id:
            query = query.eq("admin_id", admin_id)
        if action:
            query = query.eq("action", action)
        if target_type:
            query = query.eq("target_type", target_type)
        if start_date:
            query = query.gte("created_at", start_date.isoformat())
        if end_date:
            query = query.lte("created_at", end_date.isoformat())

        query = query.order("created_at", desc=True)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        rows = result.data or []
        return rows, result.count or len(rows)

    async def list_distinct_actions(self) -> list[str]:
        client = await self._client()
        result = await client.table(self.TABLE).select("action").execute()
        if not result.data:
            return []
        actions = {log["action"] for log in result.data if log.get("action")}
        return sorted(actions)

    async def list_since(self, start_date: datetime) -> list[dict[str, Any]]:
        """All audit log rows at or after start_date (used for stats)."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .gte("created_at", start_date.isoformat())
            .execute()
        )
        return result.data or []
