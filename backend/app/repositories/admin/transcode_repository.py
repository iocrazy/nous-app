"""Repository for HLS transcode admin views (resource_versions + system_settings)."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminTranscodeRepository:
    VERSIONS_TABLE = "resource_versions"
    RESOURCES_TABLE = "resources"
    MEDIA_TABLE = "parsed_media"
    SETTINGS_TABLE = "system_settings"

    VALID_SORT_FIELDS = frozenset(
        {"created_at", "file_size_bytes", "transcode_at", "resource_id"}
    )

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Stats ─────────────────────────────────────────────────────────

    async def count_total_video_versions(self) -> int:
        client = await self._client()
        result = (
            await client.table(self.VERSIONS_TABLE)
            .select("id", count="exact")
            .like("mime_type", "video/%")
            .execute()
        )
        return result.count or 0

    async def count_by_status(self, status: str) -> int:
        client = await self._client()
        result = (
            await client.table(self.VERSIONS_TABLE)
            .select("id", count="exact")
            .like("mime_type", "video/%")
            .eq("transcode_status", status)
            .execute()
        )
        return result.count or 0

    async def status_counts(self, statuses: list[str]) -> dict[str, int]:
        results = await asyncio.gather(*[self.count_by_status(s) for s in statuses])
        return dict(zip(statuses, results))

    # ─── List ──────────────────────────────────────────────────────────

    LIST_COLUMNS = (
        "id, resource_id, version_number, filename, file_size_bytes, "
        "mime_type, transcode_status, hls_path, transcode_at, created_at"
    )

    async def list_video_versions(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: Optional[str] = None,
        min_size_mb: Optional[int] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = (
            client.table(self.VERSIONS_TABLE)
            .select(self.LIST_COLUMNS, count="exact")
            .like("mime_type", "video/%")
        )

        if status_filter == "null":
            query = query.is_("transcode_status", "null")
        elif status_filter:
            query = query.eq("transcode_status", status_filter)

        if min_size_mb and min_size_mb > 0:
            query = query.gte("file_size_bytes", min_size_mb * 1024 * 1024)

        sort_field = sort_by if sort_by in self.VALID_SORT_FIELDS else "created_at"
        query = query.order(sort_field, desc=sort_desc)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0

    async def resources_to_media(self, resource_ids: list[str]) -> dict[str, str]:
        """resource_id → media_id map for a batch."""
        if not resource_ids:
            return {}
        client = await self._client()
        result = (
            await client.table(self.RESOURCES_TABLE)
            .select("id, media_id")
            .in_("id", resource_ids)
            .execute()
        )
        return {
            str(r["id"]): str(r["media_id"])
            for r in (result.data or [])
            if r.get("media_id")
        }

    async def media_info_bulk(self, media_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Lookup parsed_media rows for cover/title/author display."""
        if not media_ids:
            return {}
        client = await self._client()
        result = (
            await client.table(self.MEDIA_TABLE)
            .select(
                "id, title, cover_urls, cover_download_path, " "source_platform, author"
            )
            .in_("id", media_ids)
            .execute()
        )
        return {str(m["id"]): m for m in (result.data or [])}

    # ─── Mutations ─────────────────────────────────────────────────────

    async def get_version(self, version_id: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        try:
            result = (
                await client.table(self.VERSIONS_TABLE)
                .select("id, resource_id, mime_type")
                .eq("id", version_id)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception:
            return None

    async def mark_pending(self, version_id: str) -> None:
        client = await self._client()
        await (
            client.table(self.VERSIONS_TABLE)
            .update({"transcode_status": "pending"})
            .eq("id", version_id)
            .execute()
        )

    async def list_versions_for_batch(self, action: str) -> list[dict[str, Any]]:
        """`retry_failed` returns failed videos; `transcode_new` returns untranscoded."""
        client = await self._client()
        query = (
            client.table(self.VERSIONS_TABLE)
            .select("id, resource_id, mime_type")
            .like("mime_type", "video/%")
        )
        if action == "retry_failed":
            query = query.eq("transcode_status", "failed")
        else:  # transcode_new
            query = query.is_("transcode_status", "null")

        result = await query.execute()
        return result.data or []

    # ─── Settings ──────────────────────────────────────────────────────

    async def load_settings(self) -> dict[str, Any]:
        client = await self._client()
        result = await (
            client.table(self.SETTINGS_TABLE)
            .select("key, value")
            .like("key", "transcode_%")
            .execute()
        )
        return {row["key"]: row["value"] for row in (result.data or [])}

    async def upsert_setting(self, key: str, value: Any, updated_by: str) -> None:
        client = await self._client()
        await (
            client.table(self.SETTINGS_TABLE)
            .upsert({"key": key, "value": value, "updated_by": updated_by})
            .execute()
        )
