"""Repository for admin video management (parsed_media table)."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminVideosRepository:
    TABLE = "parsed_media"
    ALLOWED_SORT_FIELDS = frozenset(
        {"created_at", "datasize_bytes", "video_download_status"}
    )

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Stats ─────────────────────────────────────────────────────────

    async def count_total(self) -> int:
        client = await self._client()
        result = (
            await client.table(self.TABLE).select("id", count="exact").execute()
        )
        return result.count or 0

    async def count_by_status(self, status: str) -> int:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("id", count="exact")
            .eq("video_download_status", status)
            .execute()
        )
        return result.count or 0

    async def sum_storage_bytes(self) -> int:
        """Total bytes across completed media. PostgREST lacks SUM; pull sizes."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("datasize_bytes")
            .gt("datasize_bytes", 0)
            .execute()
        )
        return sum(
            (row.get("datasize_bytes") or 0) for row in (result.data or [])
        )

    async def counts_by_statuses(
        self, statuses: list[str]
    ) -> dict[str, int]:
        """Parallel fanout — one query per status but all in flight at once."""
        results = await asyncio.gather(
            *[self.count_by_status(s) for s in statuses]
        )
        return dict(zip(statuses, results))

    # ─── List / Get ────────────────────────────────────────────────────

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        video_download_status: Optional[str] = None,
        source_platform: Optional[str] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TABLE).select("*", count="exact")

        if search:
            query = query.or_(
                f"title.ilike.%{search}%,platform_id.ilike.%{search}%"
            )
        if video_download_status:
            query = query.eq("video_download_status", video_download_status)
        if source_platform:
            query = query.eq("source_platform", source_platform)

        sort_field = (
            sort_by if sort_by in self.ALLOWED_SORT_FIELDS else "created_at"
        )
        query = query.order(sort_field, desc=sort_desc)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0

    async def get_by_id(self, video_id: int) -> Optional[dict[str, Any]]:
        client = await self._client()
        try:
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", video_id)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception:
            return None

    # ─── Mutations ─────────────────────────────────────────────────────

    async def delete(self, video_id: int) -> bool:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .delete()
            .eq("id", video_id)
            .execute()
        )
        return bool(result.data)

    async def reset_for_retry(self, video_id: int) -> bool:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .update(
                {"video_download_status": "pending", "error_message": None}
            )
            .eq("id", video_id)
            .execute()
        )
        return bool(result.data)
