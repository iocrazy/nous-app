"""Repository for admin task center (unified_tasks table)."""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminTasksRepository:
    TABLE = "unified_tasks"

    LIST_COLUMNS = (
        "id, user_id, task_type, status, phase, title, subtitle, "
        "progress, speed, total_bytes, error_msg, error_code, "
        "resource_id, media_id, celery_task_id, metadata, "
        "created_at, started_at, completed_at"
    )

    async def _client(self):
        return await get_async_supabase_admin()

    async def count_total(self) -> int:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("id", count="exact")
            .execute()
        )
        return result.count or 0

    async def count_by_status(self, status: str) -> int:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("id", count="exact")
            .eq("status", status)
            .execute()
        )
        return result.count or 0

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = (
            client.table(self.TABLE)
            .select(self.LIST_COLUMNS, count="exact")
        )
        if status:
            query = query.eq("status", status)
        if task_type:
            query = query.eq("task_type", task_type)
        if search:
            pat = f"*{search}*"
            or_clauses = [
                f"title.ilike.{pat}",
                f"subtitle.ilike.{pat}",
                f"error_msg.ilike.{pat}",
                f"celery_task_id.ilike.{pat}",
                f"metadata->>original_url.ilike.{pat}",
            ]
            if search.isdigit():
                or_clauses += [
                    f"id.eq.{search}",
                    f"media_id.eq.{search}",
                    f"resource_id.eq.{search}",
                ]
            query = query.or_(",".join(or_clauses))

        query = query.order(sort_by, desc=sort_desc)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0

    async def get(self, task_id: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("id, status, celery_task_id, task_type")
            .eq("id", task_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def update(self, task_id: str, changes: dict[str, Any]) -> None:
        client = await self._client()
        await (
            client.table(self.TABLE)
            .update(changes)
            .eq("id", task_id)
            .execute()
        )
