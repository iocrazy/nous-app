"""Repository for admin table preferences (Notion-style table config)."""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminTablePreferencesRepository:
    TABLE = "admin_table_preferences"
    COLUMNS = "table_key, filters, sorts, visible_columns, column_order"

    async def _client(self):
        return await get_async_supabase_admin()

    async def get(self, user_id: str, table_key: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select(self.COLUMNS)
            .eq("user_id", user_id)
            .eq("table_key", table_key)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None

    async def upsert(
        self,
        user_id: str,
        table_key: str,
        filters: list[dict[str, Any]],
        sorts: list[dict[str, Any]],
        visible_columns: list[str] | None,
        column_order: list[str] | None,
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        payload = {
            "user_id": user_id,
            "table_key": table_key,
            "filters": filters,
            "sorts": sorts,
            "visible_columns": visible_columns,
            "column_order": column_order,
        }
        result = await (
            client.table(self.TABLE)
            .upsert(payload, on_conflict="user_id,table_key")
            .execute()
        )
        if result.data:
            return result.data[0]
        return None

    async def delete(self, user_id: str, table_key: str) -> None:
        client = await self._client()
        await (
            client.table(self.TABLE)
            .delete()
            .eq("user_id", user_id)
            .eq("table_key", table_key)
            .execute()
        )


def get_admin_table_preferences_repository() -> "AdminTablePreferencesRepository":
    """Return the right AdminTablePreferencesRepository implementation per env.

    ORM when ``USE_ORM_ADMIN_TABLE_PREFERENCES`` is set AND the SQLAlchemy engine
    is configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_ADMIN_TABLE_PREFERENCES:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.admin.table_preferences_repository_orm import (
                AdminTablePreferencesRepositoryOrm,
            )

            return AdminTablePreferencesRepositoryOrm()
        from loguru import logger

        logger.warning(
            "USE_ORM_ADMIN_TABLE_PREFERENCES=true but SUPAVISOR_DATABASE_URL is "
            "empty — falling back to supabase-py path"
        )
    return AdminTablePreferencesRepository()
