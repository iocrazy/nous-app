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
            client.table(self.TABLE).update(changes).eq("id", user_id).execute()
        )
        return result.data[0] if result.data else None

    async def set_banned(
        self, user_id: str, is_banned: bool
    ) -> Optional[dict[str, Any]]:
        return await self.update(user_id, {"is_banned": is_banned})


def get_admin_users_repository() -> "AdminUsersRepository":
    """Return the right AdminUsersRepository implementation per env.

    ORM when ``USE_ORM_ADMIN_USERS`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_ADMIN_USERS:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.admin.users_repository_orm import (
                AdminUsersRepositoryOrm,
            )

            return AdminUsersRepositoryOrm()
        from loguru import logger

        logger.warning(
            "USE_ORM_ADMIN_USERS=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return AdminUsersRepository()
