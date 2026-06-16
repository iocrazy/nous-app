"""Repository for the system_settings table (admin-only).

Wraps the raw Supabase calls that were previously inlined in
admin/settings_router.py. Centralizing them here:
- makes router code testable without a live Supabase
- keeps the "exclude transcode_* from list" policy in one place
- lets us swap storage later (e.g. cache or JSON column) without
  touching callers.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.db import get_async_supabase_admin


class SystemSettingsRepository:
    TABLE = "system_settings"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_non_transcode(self) -> list[dict[str, Any]]:
        """Return settings rows, excluding transcode_* (managed elsewhere)."""
        client = await self._client()
        result = await client.table(self.TABLE).select("*").order("key").execute()
        return [
            row
            for row in (result.data or [])
            if not row["key"].startswith("transcode_")
        ]

    async def exists(self, key: str) -> bool:
        client = await self._client()
        try:
            result = (
                await client.table(self.TABLE)
                .select("key")
                .eq("key", key)
                .maybe_single()
                .execute()
            )
            return result.data is not None
        except Exception as e:
            logger.warning(f"[SystemSettings] exists({key}) failed: {e}")
            return False

    async def update(
        self, key: str, value: Any, updated_by: str
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.TABLE)
            .update({"value": value, "updated_by": updated_by})
            .eq("key", key)
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]

    async def upsert_setting(
        self, key: str, value: Any, updated_by: str
    ) -> dict[str, Any]:
        """Insert-or-update a setting row (safe for first-time writes with no seed).

        Uses the supabase-py ``.upsert()`` which maps to PostgreSQL
        ``INSERT ... ON CONFLICT DO UPDATE``.  The ``key`` column is the primary
        key; a collision updates ``value`` and ``updated_by``.
        """
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .upsert({"key": key, "value": value, "updated_by": updated_by})
            .execute()
        )
        return result.data[0] if result.data else {"key": key, "value": value}


def get_system_settings_repository() -> "SystemSettingsRepository":
    """Return the right SystemSettingsRepository implementation per env.

    ORM when ``USE_ORM_ADMIN_SYSTEM_SETTINGS`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings
    from app.db.engine import is_configured

    if settings.USE_ORM_ADMIN_SYSTEM_SETTINGS:
        if is_configured():
            from app.repositories.admin.system_settings_repository_orm import (
                SystemSettingsRepositoryOrm,
            )

            return SystemSettingsRepositoryOrm()
        logger.warning(
            "USE_ORM_ADMIN_SYSTEM_SETTINGS=true but SUPAVISOR_DATABASE_URL is "
            "empty — falling back to supabase-py path"
        )

    from app.db.shadow_compare import shadow_enabled

    if shadow_enabled("admin_system_settings") and is_configured():
        from app.db.shadow_compare import ShadowRepo
        from app.repositories.admin.system_settings_repository_orm import (
            SystemSettingsRepositoryOrm,
        )

        return ShadowRepo(
            SystemSettingsRepository(),
            SystemSettingsRepositoryOrm(),
            "admin_system_settings",
        )

    return SystemSettingsRepository()
