"""Repository for Smart Collections data access (异步)."""

from datetime import datetime
from typing import List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class CollectionsRepository:
    """Repository for smart collections CRUD operations (异步)."""

    TABLE_NAME = "smart_collections"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def get_all_collections(self, user_id: str) -> List[dict]:
        """Get all collections for a user."""
        table = await self._get_table()
        result = (
            await table.select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data

    async def get_collection_by_id(
        self, collection_id: str, user_id: str
    ) -> Optional[dict]:
        """Get a single collection by ID."""
        table = await self._get_table()
        result = (
            await table.select("*")
            .eq("id", collection_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def create_collection(
        self,
        user_id: str,
        name: str,
        rules: dict,
        icon: str = "📁",
        description: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> dict:
        """Create a new smart collection."""
        data = {
            "user_id": user_id,
            "name": name,
            "icon": icon,
            "description": description,
            "rules": rules,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "cached_count": 0,
            "is_preset": False,
        }

        table = await self._get_table()
        result = await table.insert(data).execute()
        logger.info(f"Created collection: {name} for user: {user_id}")
        return result.data[0]

    async def update_collection(
        self, collection_id: str, user_id: str, **kwargs
    ) -> Optional[dict]:
        """Update a collection."""
        # Filter out None values
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_collection_by_id(collection_id, user_id)

        update_data["updated_at"] = datetime.utcnow().isoformat()

        table = await self._get_table()
        result = (
            await table.update(update_data)
            .eq("id", collection_id)
            .eq("user_id", user_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def delete_collection(self, collection_id: str, user_id: str) -> bool:
        """Delete a collection (non-preset only)."""
        table = await self._get_table()
        result = (
            await table.delete()
            .eq("id", collection_id)
            .eq("user_id", user_id)
            .eq("is_preset", False)
            .execute()
        )
        return len(result.data) > 0

    async def update_cache(
        self, collection_id: str, media_ids: List[int], count: int
    ) -> dict:
        """Update the cached media IDs and count for a collection."""
        table = await self._get_table()
        result = (
            await table.update(
                {
                    "cached_media_ids": media_ids,
                    "cached_count": count,
                    "cached_at": datetime.utcnow().isoformat(),
                }
            )
            .eq("id", collection_id)
            .execute()
        )

        return result.data[0] if result.data else None

    async def get_preset_collections(self, user_id: str) -> List[dict]:
        """Get preset collections for a user."""
        table = await self._get_table()
        result = (
            await table.select("*")
            .eq("user_id", user_id)
            .eq("is_preset", True)
            .execute()
        )
        return result.data

    async def create_default_presets(self, user_id: str) -> List[dict]:
        """Create default preset collections for a new user."""
        presets = [
            {
                "name": "Recent Downloads",
                "icon": "📥",
                "description": "Videos downloaded in the last 7 days",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "date", "operator": "gte", "value": "7_days_ago"}
                    ],
                },
                "is_preset": True,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            {
                "name": "Favorites",
                "icon": "⭐",
                "description": "Videos marked as keep forever",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "keep_forever", "operator": "equals", "value": True}
                    ],
                },
                "is_preset": True,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            {
                "name": "Most Viewed",
                "icon": "👀",
                "description": "Videos you view frequently",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "view_count", "operator": "gte", "value": 3}
                    ],
                },
                "is_preset": True,
                "sort_by": "view_count",
                "sort_order": "desc",
            },
            {
                "name": "Untagged",
                "icon": "🏷️",
                "description": "Videos without any tags",
                "rules": {
                    "match": "all",
                    "conditions": [
                        {"field": "tag_count", "operator": "equals", "value": 0}
                    ],
                },
                "is_preset": True,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
        ]

        table = await self._get_table()
        created = []
        for preset in presets:
            data = {"user_id": user_id, **preset, "cached_count": 0}
            result = await table.insert(data).execute()
            if result.data:
                created.append(result.data[0])

        logger.info(f"Created {len(created)} preset collections for user {user_id}")
        return created
