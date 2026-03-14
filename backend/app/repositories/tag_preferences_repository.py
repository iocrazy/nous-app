"""Repository for user tag picker preferences."""

from app.db.supabase_client import get_async_supabase_admin


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
            "picker_settings": {**self.DEFAULTS["picker_settings"], **(row.get("picker_settings") or {})},
            "panel_size": {**self.DEFAULTS["panel_size"], **(row.get("panel_size") or {})},
        }

    async def upsert_preferences(self, user_id: str, updates: dict) -> dict:
        """Upsert preferences. Merges picker_settings at field level."""
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
            data["panel_size"] = size if isinstance(size, dict) else size.model_dump()

        result = (
            await client.table("user_tag_preferences")
            .upsert(data, on_conflict="user_id")
            .execute()
        )

        return await self.get_preferences(user_id)
