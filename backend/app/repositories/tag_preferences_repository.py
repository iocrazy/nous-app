"""Repository for user tag picker preferences.

ORM 2.0 migration (Batch L1): ``TagPreferencesRepository`` is the legacy
supabase-py REST implementation; ``TagPreferencesRepositoryOrm`` (in
``tag_preferences_repository_orm.py``) is the SQLAlchemy 2.0 ORM successor.
Call sites go through ``get_tag_preferences_repository()`` (bottom of this file)
which picks the ORM subclass when the SQLAlchemy engine is configured
(SUPAVISOR_DATABASE_URL is set), otherwise falls back to the legacy path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.tag_preferences_repository_orm import (
        TagPreferencesRepositoryOrm,
    )


class TagPreferencesRepository:
    """Data access for user_tag_preferences table."""

    DEFAULTS = {
        "starred_tag_ids": [],
        "picker_settings": {
            "layout": "list",
            "columnWidth": "medium",
            "showStarred": True,
            "showRecently": True,
            "showRecommended": False,
            "showCount": True,
        },
        "panel_size": {"width": 480, "height": 400},
    }

    async def get_preferences(self, user_id: str) -> dict:
        """Get preferences for a user. Returns defaults if not found."""
        try:
            client = await get_async_supabase_admin()
            result = (
                await client.table("user_tag_preferences")
                .select("starred_tag_ids, picker_settings, panel_size")
                .eq("user_id", user_id)
                .maybe_single()
                .execute()
            )

            if not result.data:
                return dict(self.DEFAULTS)

            row = result.data
            return {
                "starred_tag_ids": row.get("starred_tag_ids") or [],
                "picker_settings": {
                    **self.DEFAULTS["picker_settings"],
                    **(row.get("picker_settings") or {}),
                },
                "panel_size": {
                    **self.DEFAULTS["panel_size"],
                    **(row.get("panel_size") or {}),
                },
            }
        except Exception:
            # Table may not exist yet — return defaults gracefully
            return dict(self.DEFAULTS)

    async def upsert_preferences(self, user_id: str, updates: dict) -> dict:
        """Upsert preferences. Merges picker_settings at field level."""
        try:
            client = await get_async_supabase_admin()

            # Get current to merge
            current = await self.get_preferences(user_id)

            # Build upsert data
            data = {"user_id": user_id}

            if "starred_tag_ids" in updates and updates["starred_tag_ids"] is not None:
                data["starred_tag_ids"] = updates["starred_tag_ids"]

            if "picker_settings" in updates and updates["picker_settings"] is not None:
                merged = {**current["picker_settings"], **updates["picker_settings"]}
                data["picker_settings"] = merged

            if "panel_size" in updates and updates["panel_size"] is not None:
                size = updates["panel_size"]
                data["panel_size"] = (
                    size if isinstance(size, dict) else size.model_dump()
                )

            await client.table("user_tag_preferences").upsert(
                data, on_conflict="user_id"
            ).execute()

            return await self.get_preferences(user_id)
        except Exception:
            # Table may not exist yet — return defaults
            return dict(self.DEFAULTS)


def get_tag_preferences_repository() -> (
    Union["TagPreferencesRepository", "TagPreferencesRepositoryOrm"]
):
    """Return the right TagPreferencesRepository implementation per env.

    ORM when the SQLAlchemy engine is configured (SUPAVISOR_DATABASE_URL set);
    otherwise the legacy supabase-py REST path. An engine-missing deploy logs
    once and falls back to REST (never crashes).
    """
    from app.db import engine as db_engine

    if db_engine.is_configured():
        from app.repositories.tag_preferences_repository_orm import (
            TagPreferencesRepositoryOrm,
        )

        return TagPreferencesRepositoryOrm()
    logger.warning(
        "SUPAVISOR_DATABASE_URL is empty "
        "— falling back to supabase-py path for tag_preferences"
    )
    return TagPreferencesRepository()
