"""Smart Collections service with rule engine (异步)."""

from datetime import datetime, timedelta
from typing import Any, Dict, List

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.repositories.collections_repository import CollectionsRepository


class CollectionsService:
    """Service for smart collection rule evaluation and media matching (异步)."""

    def __init__(self):
        self.repo = CollectionsRepository()

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_user_resource_mapping(self, user_id: str) -> Dict[int, int]:
        """Get user's resource mapping {resource_id: media_id} from resources table.

        Since parsed_media is now a global table (no user_id column),
        we identify the user's media through the resources table.
        """
        client = await self._get_client()
        result = (
            await client.table("resources")
            .select("id, media_id")
            .eq("creator_id", user_id)
            .eq("source_type", "web")
            .eq("is_trashed", False)
            .execute()
        )
        return {
            r["id"]: r["media_id"]
            for r in result.data
            if r.get("media_id")
        }

    async def get_collection_media(
        self,
        collection_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        use_cache: bool = True,
    ) -> tuple[List[dict], int]:
        """
        Get media matching a collection's rules.

        Returns tuple of (media_list, total_count).
        """
        collection = await self.repo.get_collection_by_id(collection_id, user_id)
        if not collection:
            return [], 0

        rules = collection.get("rules", {})

        # Check if cache is valid (less than 5 minutes old)
        cached_at = collection.get("cached_at")
        if use_cache and cached_at:
            cache_age = datetime.utcnow() - datetime.fromisoformat(
                cached_at.replace("Z", "+00:00").replace("+00:00", "")
            )
            if cache_age < timedelta(minutes=5) and collection.get("cached_media_ids"):
                # Use cached media IDs
                media_ids = collection["cached_media_ids"]
                total = collection.get("cached_count", len(media_ids))

                # Paginate
                start = (page - 1) * page_size
                end = start + page_size
                page_ids = media_ids[start:end]

                if page_ids:
                    media_list = await self._fetch_media_by_ids(page_ids, collection)
                    return media_list, total

        # Evaluate rules to get matching media
        media_ids = await self._evaluate_rules(rules, user_id)
        total = len(media_ids)

        # Update cache
        await self.repo.update_cache(
            collection_id, media_ids[:1000], total
        )  # Cache up to 1000 IDs

        # Paginate
        start = (page - 1) * page_size
        end = start + page_size
        page_ids = media_ids[start:end]

        if not page_ids:
            return [], total

        media_list = await self._fetch_media_by_ids(page_ids, collection)
        return media_list, total

    async def _evaluate_rules(self, rules: dict, user_id: str) -> List[int]:
        """Evaluate collection rules and return matching media IDs."""
        match_type = rules.get("match", "all")
        conditions = rules.get("conditions", [])

        # Get user's media IDs through resources table
        resource_map = await self._get_user_resource_mapping(user_id)
        user_media_ids = list(resource_map.values())

        if not user_media_ids:
            return []

        if not conditions:
            # No conditions = all user media
            return user_media_ids

        # Start with base query
        media_sets = []

        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")

            matching_ids = await self._evaluate_condition(
                field, operator, value, user_id, user_media_ids, resource_map
            )
            media_sets.append(set(matching_ids))

        if not media_sets:
            return []

        # Combine sets based on match type
        if match_type == "all":
            result_set = media_sets[0]
            for s in media_sets[1:]:
                result_set = result_set.intersection(s)
        else:  # "any"
            result_set = set()
            for s in media_sets:
                result_set = result_set.union(s)

        return list(result_set)

    async def _evaluate_condition(
        self,
        field: str,
        operator: str,
        value: Any,
        user_id: str,
        user_media_ids: List[int],
        resource_map: Dict[int, int],
    ) -> List[int]:
        """Evaluate a single condition and return matching media IDs."""
        client = await self._get_client()

        # Tag-based conditions
        if field == "tag":
            return await self._match_tag_condition(
                operator, value, resource_map
            )

        # Date-based conditions
        if field == "date":
            return await self._match_date_condition(
                operator, value, user_media_ids
            )

        # Direct field conditions on parsed_media (scoped to user's media)
        query = client.table("parsed_media").select("id").in_("id", user_media_ids)

        field_mapping = {
            "author": "author",
            "title": "title",
            "description": "description",
            "keep_forever": "keep_forever",
            "view_count": "view_count",
            "media_type": "media_type",
        }

        db_field = field_mapping.get(field, field)

        if operator == "equals":
            query = query.eq(db_field, value)
        elif operator == "contains":
            query = query.ilike(db_field, f"%{value}%")
        elif operator == "starts_with":
            query = query.ilike(db_field, f"{value}%")
        elif operator == "gt":
            query = query.gt(db_field, value)
        elif operator == "gte":
            query = query.gte(db_field, value)
        elif operator == "lt":
            query = query.lt(db_field, value)
        elif operator == "lte":
            query = query.lte(db_field, value)
        elif operator == "in":
            if isinstance(value, list):
                query = query.in_(db_field, value)

        result = await query.execute()
        return [r["id"] for r in result.data]

    async def _match_tag_condition(
        self, operator: str, value: Any, resource_map: Dict[int, int]
    ) -> List[int]:
        """Match media by tag conditions.

        resource_tags.resource_id references resources.id,
        so we query by user's resource IDs and map back to media IDs.
        """
        client = await self._get_client()

        resource_ids = list(resource_map.keys())
        if not resource_ids:
            return []

        if operator == "has":
            # Media that have a specific tag
            result = (
                await client.table("resource_tags")
                .select("resource_id")
                .eq("tag_id", value)
                .in_("resource_id", resource_ids)
                .execute()
            )
            # Map resource_id back to media_id
            return list(set(
                resource_map[r["resource_id"]]
                for r in result.data
                if r["resource_id"] in resource_map
            ))

        elif operator == "has_any":
            # Media that have any of the specified tags
            if isinstance(value, list):
                result = (
                    await client.table("resource_tags")
                    .select("resource_id")
                    .in_("tag_id", value)
                    .in_("resource_id", resource_ids)
                    .execute()
                )
                return list(set(
                    resource_map[r["resource_id"]]
                    for r in result.data
                    if r["resource_id"] in resource_map
                ))

        elif operator == "has_all":
            # Media that have all of the specified tags
            if isinstance(value, list):
                media_tag_counts: Dict[int, int] = {}
                result = (
                    await client.table("resource_tags")
                    .select("resource_id")
                    .in_("tag_id", value)
                    .in_("resource_id", resource_ids)
                    .execute()
                )
                for r in result.data:
                    rid = r["resource_id"]
                    if rid in resource_map:
                        mid = resource_map[rid]
                        media_tag_counts[mid] = media_tag_counts.get(mid, 0) + 1
                return [
                    mid
                    for mid, count in media_tag_counts.items()
                    if count == len(value)
                ]

        return []

    async def _match_date_condition(
        self, operator: str, value: Any, user_media_ids: List[int]
    ) -> List[int]:
        """Match media by date conditions."""
        client = await self._get_client()

        if not user_media_ids:
            return []

        query = client.table("parsed_media").select("id").in_("id", user_media_ids)

        # Handle relative date values
        if isinstance(value, str):
            if value.endswith("_days_ago"):
                days = int(value.replace("_days_ago", ""))
                value = (datetime.utcnow() - timedelta(days=days)).isoformat()
            elif value.endswith("_weeks_ago"):
                weeks = int(value.replace("_weeks_ago", ""))
                value = (datetime.utcnow() - timedelta(weeks=weeks)).isoformat()
            elif value.endswith("_months_ago"):
                months = int(value.replace("_months_ago", ""))
                value = (datetime.utcnow() - timedelta(days=months * 30)).isoformat()

        if operator == "gte":
            query = query.gte("created_at", value)
        elif operator == "lte":
            query = query.lte("created_at", value)
        elif operator == "gt":
            query = query.gt("created_at", value)
        elif operator == "lt":
            query = query.lt("created_at", value)

        result = await query.execute()
        return [r["id"] for r in result.data]

    async def _fetch_media_by_ids(
        self, media_ids: List[int], collection: dict
    ) -> List[dict]:
        """Fetch full media details for given IDs."""
        if not media_ids:
            return []

        client = await self._get_client()
        sort_by = collection.get("sort_by", "created_at")
        sort_order = collection.get("sort_order", "desc")

        result = (
            await client.table("parsed_media")
            .select(
                "id, title, description, author, cover_url, duration, media_type, created_at, view_count, keep_forever"
            )
            .in_("id", media_ids)
            .order(sort_by, desc=(sort_order == "desc"))
            .execute()
        )

        return result.data

    async def refresh_collection_cache(self, collection_id: str, user_id: str) -> int:
        """Force refresh a collection's cache."""
        collection = await self.repo.get_collection_by_id(collection_id, user_id)
        if not collection:
            return 0

        rules = collection.get("rules", {})
        media_ids = await self._evaluate_rules(rules, user_id)
        total = len(media_ids)

        await self.repo.update_cache(collection_id, media_ids[:1000], total)

        logger.info(f"Refreshed cache for collection {collection_id}: {total} media")
        return total
