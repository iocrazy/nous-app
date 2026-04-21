"""Repository for admin tags + tag_groups management."""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminTagsRepository:
    TAGS_TABLE = "tags"
    GROUPS_TABLE = "tag_groups"
    RESOURCE_TAGS_TABLE = "resource_tags"

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Tag groups ────────────────────────────────────────────────────

    async def list_groups(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.GROUPS_TABLE)
            .select("*")
            .order("sort_order")
            .order("created_at")
            .execute()
        )
        return result.data or []

    async def max_group_sort_order(self) -> int:
        client = await self._client()
        result = (
            await client.table(self.GROUPS_TABLE)
            .select("sort_order")
            .order("sort_order", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0]["sort_order"] if result.data else 0

    async def create_group(
        self, name: str, sort_order: int
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.GROUPS_TABLE)
            .insert({"name": name, "sort_order": sort_order})
            .execute()
        )
        return result.data[0] if result.data else None

    async def update_group(
        self, group_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.GROUPS_TABLE)
            .update(changes)
            .eq("id", group_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def delete_group(self, group_id: str) -> bool:
        client = await self._client()
        result = (
            await client.table(self.GROUPS_TABLE).delete().eq("id", group_id).execute()
        )
        return bool(result.data)

    async def reorder_groups(self, group_ids: list[str]) -> None:
        client = await self._client()
        for idx, gid in enumerate(group_ids):
            await (
                client.table(self.GROUPS_TABLE)
                .update({"sort_order": idx})
                .eq("id", gid)
                .execute()
            )

    # ─── Tags ──────────────────────────────────────────────────────────

    async def list_tags(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        group_id: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TAGS_TABLE).select(
            "*, tag_groups(name)", count="exact"
        )

        if group_id == "uncategorized":
            query = query.is_("group_id", "null")
        elif group_id:
            query = query.eq("group_id", group_id)

        if search:
            query = query.or_(f"name.ilike.%{search}%,name_zh.ilike.%{search}%")

        if sort_by and sort_order:
            query = query.order(sort_by, desc=(sort_order == "desc"))
        else:
            query = query.order("sort_order").order("created_at", desc=True)

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = await query.execute()
        return result.data or [], result.count or 0

    async def all_tag_group_ids(self) -> list[dict[str, Any]]:
        """Used by list_groups to compute tag counts per group."""
        client = await self._client()
        result = await client.table(self.TAGS_TABLE).select("group_id").execute()
        return result.data or []

    async def usage_counts(self, tag_ids: list[str]) -> dict[str, int]:
        """resource_tags rows grouped by tag_id."""
        if not tag_ids:
            return {}
        client = await self._client()
        result = (
            await client.table(self.RESOURCE_TAGS_TABLE)
            .select("tag_id")
            .in_("tag_id", tag_ids)
            .execute()
        )
        counts: dict[str, int] = {}
        for row in result.data or []:
            tid = str(row["tag_id"])
            counts[tid] = counts.get(tid, 0) + 1
        return counts

    async def create_tag(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = await client.table(self.TAGS_TABLE).insert(payload).execute()
        return result.data[0] if result.data else None

    async def update_tag(
        self, tag_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TAGS_TABLE)
            .update(changes)
            .eq("id", tag_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag plus its resource_tags associations."""
        client = await self._client()
        await (
            client.table(self.RESOURCE_TAGS_TABLE)
            .delete()
            .eq("tag_id", tag_id)
            .execute()
        )
        result = await client.table(self.TAGS_TABLE).delete().eq("id", tag_id).execute()
        return bool(result.data)

    # ─── Batch ─────────────────────────────────────────────────────────

    async def batch_set_group(
        self, tag_ids: list[str], group_id: Optional[str]
    ) -> None:
        client = await self._client()
        for tid in tag_ids:
            await (
                client.table(self.TAGS_TABLE)
                .update({"group_id": group_id})
                .eq("id", tid)
                .execute()
            )

    async def batch_set_color(self, tag_ids: list[str], color: str) -> None:
        client = await self._client()
        for tid in tag_ids:
            await (
                client.table(self.TAGS_TABLE)
                .update({"color": color})
                .eq("id", tid)
                .execute()
            )

    async def batch_delete(self, tag_ids: list[str]) -> None:
        client = await self._client()
        for tid in tag_ids:
            await (
                client.table(self.RESOURCE_TAGS_TABLE)
                .delete()
                .eq("tag_id", tid)
                .execute()
            )
            await client.table(self.TAGS_TABLE).delete().eq("id", tid).execute()

    async def reorder_tags(self, tag_ids: list[str]) -> None:
        client = await self._client()
        for idx, tid in enumerate(tag_ids):
            await (
                client.table(self.TAGS_TABLE)
                .update({"sort_order": idx})
                .eq("id", tid)
                .execute()
            )
