"""Repository for admin user management (user_profiles table)."""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminUsersRepository:
    TABLE = "user_profiles"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        role: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TABLE).select("*", count="exact")
        if search:
            query = query.ilike("username", f"%{search}%")
        if role:
            query = query.eq("role", role)

        offset = (page - 1) * page_size
        result = await (
            query.order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        return result.data or [], result.count or 0

    async def get_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        try:
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception:
            return None

    async def exists(self, user_id: str) -> bool:
        client = await self._client()
        try:
            result = (
                await client.table(self.TABLE)
                .select("id")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            return result.data is not None
        except Exception:
            return False

    async def update(
        self, user_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.TABLE)
            .update(changes)
            .eq("id", user_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def set_banned(
        self, user_id: str, is_banned: bool
    ) -> Optional[dict[str, Any]]:
        return await self.update(user_id, {"is_banned": is_banned})
