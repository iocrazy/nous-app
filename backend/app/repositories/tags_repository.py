"""Repository for Tags data access (异步)."""

from typing import List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class TagsRepository:
    """Repository for tags CRUD operations (异步)."""

    TABLE_NAME = "tags"
    RESOURCE_TAGS_TABLE = "resource_tags"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def _get_resource_tags_table(self):
        """获取资源标签关联表引用"""
        client = await self._get_client()
        return client.table(self.RESOURCE_TAGS_TABLE)

    async def get_all_tags(
        self, user_id: Optional[str] = None, enabled_only: bool = False
    ) -> List[dict]:
        """Get all tags (system + time + user's own tags) with media_count and group info.

        Optimized: single query for tags + single RPC call for counts (was N+1).
        """
        table = await self._get_table()
        client = await self._get_client()

        # Build filter: system, time, and optionally user tags
        # Use a single query with or_ filter instead of 3 separate queries
        select_fields = "*, tag_groups(name)"
        query = table.select(select_fields)

        if user_id:
            query = query.or_(f"type.eq.system,type.eq.time,and(type.eq.user,user_id.eq.{user_id})")
        else:
            query = query.or_("type.eq.system,type.eq.time")

        if enabled_only:
            try:
                query = query.eq("enabled", True)
            except Exception:
                logger.warning("'enabled' column not found, skipping filter")

        result = await query.execute()
        tags = result.data

        # Batch count: get all tag usage counts in one query via RPC or aggregation
        tag_ids = [str(t["id"]) for t in tags]
        count_map: dict[str, int] = {}
        if tag_ids:
            rt_table = client.table("resource_tags")
            count_result = await rt_table.select("tag_id").in_("tag_id", tag_ids).execute()
            for row in count_result.data:
                tid = str(row["tag_id"])
                count_map[tid] = count_map.get(tid, 0) + 1

        # Flatten group info and attach counts
        for tag in tags:
            group_data = tag.pop("tag_groups", None)
            tag["group_name"] = group_data.get("name") if group_data else None
            if "enabled" not in tag:
                tag["enabled"] = True
            tag["media_count"] = count_map.get(str(tag["id"]), 0)

        return tags

    async def get_tag_by_id(self, tag_id: str) -> Optional[dict]:
        """Get a single tag by ID."""
        table = await self._get_table()
        result = await table.select("*").eq("id", tag_id).maybe_single().execute()
        return result.data if result.data else None

    async def get_tag_by_name(
        self, name: str, user_id: Optional[str] = None
    ) -> Optional[dict]:
        """Get a tag by name or name_zh (checks system tags first, then user tags).

        For system/time tags, matches both 'name' (English) and 'name_zh' (Chinese).
        This prevents duplicate tags when Chinese names are passed for existing English system tags.
        """
        table = await self._get_table()

        try:
            # Check system tags — match by English name OR Chinese alias
            result = (
                await table.select("*")
                .or_(f"name.eq.{name},name_zh.eq.{name}")
                .eq("type", "system")
                .limit(1)
                .execute()
            )
            if result and result.data and len(result.data) > 0:
                return result.data[0]

            # Check time tags — match by English name OR Chinese alias
            result = (
                await table.select("*")
                .or_(f"name.eq.{name},name_zh.eq.{name}")
                .eq("type", "time")
                .limit(1)
                .execute()
            )
            if result and result.data and len(result.data) > 0:
                return result.data[0]

            # Check user tags (by exact name only)
            if user_id:
                result = (
                    await table.select("*")
                    .eq("name", name)
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                if result and result.data and len(result.data) > 0:
                    return result.data[0]
        except Exception as e:
            logger.error(f"Error in get_tag_by_name: {e}")

        return None

    async def create_tag(
        self,
        name: str,
        user_id: str,
        color: str = "#6366f1",
        icon: Optional[str] = None,
        name_zh: Optional[str] = None,
    ) -> dict:
        """Create a new user tag with optional Chinese name."""
        data = {
            "name": name,
            "type": "user",
            "user_id": user_id,
            "color": color,
            "icon": icon,
        }
        if name_zh:
            data["name_zh"] = name_zh

        table = await self._get_table()
        result = await table.insert(data).execute()
        logger.info(f"Created tag: {name} (zh: {name_zh}) for user: {user_id}")
        return result.data[0]

    async def update_tag(self, tag_id: str, user_id: str, **kwargs) -> Optional[dict]:
        """Update a user tag."""
        # Filter out None values
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_tag_by_id(tag_id)

        table = await self._get_table()
        result = (
            await table.update(update_data)
            .eq("id", tag_id)
            .eq("user_id", user_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def update_tag_admin(self, tag_id: str, **kwargs) -> Optional[dict]:
        """Update any tag (no user_id check). Used for toggling enabled on system tags."""
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_tag_by_id(tag_id)

        table = await self._get_table()
        result = (
            await table.update(update_data)
            .eq("id", tag_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def delete_tag(self, tag_id: str, user_id: str) -> bool:
        """Delete a user tag."""
        table = await self._get_table()
        result = (
            await table.delete()
            .eq("id", tag_id)
            .eq("user_id", user_id)
            .eq("type", "user")
            .execute()
        )
        return len(result.data) > 0

    async def add_tag_to_resource(
        self,
        resource_id: str,
        tag_id: str,
        confidence: Optional[float] = None,
        source: str = "manual",
    ) -> dict:
        """Add a tag to a resource item.

        Args:
            resource_id: The resource UUID
            tag_id: The tag UUID
            confidence: Optional confidence score for auto-assigned tags
            source: How the tag was added ('manual', 'auto', 'ai')
        """
        data = {"resource_id": resource_id, "tag_id": tag_id, "source": source}
        if confidence is not None:
            data["confidence"] = confidence

        resource_tags_table = await self._get_resource_tags_table()
        result = await resource_tags_table.upsert(data).execute()
        logger.info(f"Added tag {tag_id} to resource {resource_id}")
        return result.data[0]

    async def remove_tag_from_resource(self, resource_id: str, tag_id: str) -> bool:
        """Remove a tag from a resource item."""
        resource_tags_table = await self._get_resource_tags_table()
        result = (
            await resource_tags_table.delete()
            .eq("resource_id", resource_id)
            .eq("tag_id", tag_id)
            .execute()
        )
        return len(result.data) > 0

    async def resolve_media_id_to_resource_id(self, media_id: str) -> Optional[str]:
        """Resolve a parsed_media ID to its corresponding resource ID.

        Args:
            media_id: The parsed_media Snowflake ID

        Returns:
            The resource Snowflake ID, or None if no resource exists for this media.
        """
        client = await self._get_client()
        result = (
            await client.table("resources")
            .select("id")
            .eq("media_id", media_id)
            .limit(1)
            .execute()
        )
        if result.data and len(result.data) > 0:
            return str(result.data[0]["id"])
        return None

    async def get_resource_tags(self, resource_id: str) -> List[dict]:
        """Get all tags for a resource item with tag details."""
        resource_tags_table = await self._get_resource_tags_table()
        result = (
            await resource_tags_table.select("*, tags(*)")
            .eq("resource_id", resource_id)
            .execute()
        )

        return result.data

    async def get_resources_by_tag(
        self, tag_id: str, user_id: str, limit: int = 50, offset: int = 0
    ) -> List[dict]:
        """Get all resources with a specific tag."""
        resource_tags_table = await self._get_resource_tags_table()
        result = (
            await resource_tags_table.select("resource_id, resources(*)")
            .eq("tag_id", tag_id)
            .range(offset, offset + limit - 1)
            .execute()
        )

        return [r["resources"] for r in result.data if r.get("resources")]

    async def bulk_add_tags_to_resource(
        self, resource_id: str, tag_ids: List[str], source: str = "manual"
    ) -> List[dict]:
        """Add multiple tags to a resource item at once."""
        data = [
            {"resource_id": resource_id, "tag_id": tag_id, "source": source}
            for tag_id in tag_ids
        ]

        resource_tags_table = await self._get_resource_tags_table()
        result = await resource_tags_table.upsert(data).execute()
        logger.info(f"Added {len(tag_ids)} tags to resource {resource_id}")
        return result.data

    async def get_tag_counts(self, user_id: str, limit: int = 10) -> List[dict]:
        """Get tag usage counts for a user's resources.

        Returns tags sorted by usage count (most used first).
        """
        client = await self._get_client()

        # Query resource_tags joined with tags and resources to filter by user
        # We need to count how many resources each tag is associated with for this user
        result = await client.rpc(
            "get_user_tag_counts", {"p_user_id": user_id, "p_limit": limit}
        ).execute()

        if result.data:
            return result.data

        # Fallback: manual query if RPC doesn't exist
        logger.warning("RPC get_user_tag_counts not found, using fallback query")
        return await self._get_tag_counts_fallback(user_id, limit)

    async def _get_tag_counts_fallback(
        self, user_id: str, limit: int = 10
    ) -> List[dict]:
        """Fallback method to get tag counts without RPC."""
        client = await self._get_client()

        # Get all resource IDs for this user
        resource_result = (
            await client.table("resources").select("id").eq("user_id", user_id).execute()
        )
        if not resource_result.data:
            return []

        resource_ids = [v["id"] for v in resource_result.data]

        # Get all resource_tags for these resources
        resource_tags_result = (
            await client.table("resource_tags")
            .select("tag_id, tags(id, name, color, icon, type)")
            .in_("resource_id", resource_ids)
            .execute()
        )

        if not resource_tags_result.data:
            return []

        # Count tags
        tag_counts: dict = {}
        for vt in resource_tags_result.data:
            tag_info = vt.get("tags")
            if tag_info:
                tag_id = tag_info["id"]
                if tag_id not in tag_counts:
                    tag_counts[tag_id] = {
                        "id": tag_id,
                        "name": tag_info["name"],
                        "color": tag_info.get("color", "#6366f1"),
                        "icon": tag_info.get("icon"),
                        "type": tag_info.get("type", "system"),
                        "count": 0,
                    }
                tag_counts[tag_id]["count"] += 1

        # Sort by count and limit
        sorted_tags = sorted(
            tag_counts.values(), key=lambda x: x["count"], reverse=True
        )
        return sorted_tags[:limit]
