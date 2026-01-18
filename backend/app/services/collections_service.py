"""Smart Collections service with rule engine."""
from typing import Optional, List, Any
from datetime import datetime, timedelta

from loguru import logger

from app.db.supabase_client import get_supabase_admin
from app.repositories.collections_repository import CollectionsRepository


class CollectionsService:
    """Service for smart collection rule evaluation and video matching."""

    def __init__(self):
        self.repo = CollectionsRepository()
        self.supabase = get_supabase_admin()

    async def get_collection_videos(
        self,
        collection_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        use_cache: bool = True
    ) -> tuple[List[dict], int]:
        """
        Get videos matching a collection's rules.

        Returns tuple of (videos, total_count).
        """
        collection = await self.repo.get_collection_by_id(collection_id, user_id)
        if not collection:
            return [], 0

        rules = collection.get("rules", {})

        # Check if cache is valid (less than 5 minutes old)
        cached_at = collection.get("cached_at")
        if use_cache and cached_at:
            cache_age = datetime.utcnow() - datetime.fromisoformat(cached_at.replace("Z", "+00:00").replace("+00:00", ""))
            if cache_age < timedelta(minutes=5) and collection.get("cached_video_ids"):
                # Use cached video IDs
                video_ids = collection["cached_video_ids"]
                total = collection.get("cached_count", len(video_ids))

                # Paginate
                start = (page - 1) * page_size
                end = start + page_size
                page_ids = video_ids[start:end]

                if page_ids:
                    videos = await self._fetch_videos_by_ids(page_ids, collection)
                    return videos, total

        # Evaluate rules to get matching videos
        video_ids = await self._evaluate_rules(rules, user_id)
        total = len(video_ids)

        # Update cache
        await self.repo.update_cache(collection_id, video_ids[:1000], total)  # Cache up to 1000 IDs

        # Paginate
        start = (page - 1) * page_size
        end = start + page_size
        page_ids = video_ids[start:end]

        if not page_ids:
            return [], total

        videos = await self._fetch_videos_by_ids(page_ids, collection)
        return videos, total

    async def _evaluate_rules(self, rules: dict, user_id: str) -> List[int]:
        """Evaluate collection rules and return matching video IDs."""
        match_type = rules.get("match", "all")
        conditions = rules.get("conditions", [])

        if not conditions:
            # No conditions = all user videos
            result = self.supabase.table("douyin_videos").select("id").eq("user_id", user_id).execute()
            return [r["id"] for r in result.data]

        # Start with base query
        video_sets = []

        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")

            matching_ids = await self._evaluate_condition(field, operator, value, user_id)
            video_sets.append(set(matching_ids))

        if not video_sets:
            return []

        # Combine sets based on match type
        if match_type == "all":
            result_set = video_sets[0]
            for s in video_sets[1:]:
                result_set = result_set.intersection(s)
        else:  # "any"
            result_set = set()
            for s in video_sets:
                result_set = result_set.union(s)

        return list(result_set)

    async def _evaluate_condition(
        self,
        field: str,
        operator: str,
        value: Any,
        user_id: str
    ) -> List[int]:
        """Evaluate a single condition and return matching video IDs."""

        # Tag-based conditions
        if field == "tag":
            return await self._match_tag_condition(operator, value, user_id)

        # Date-based conditions
        if field == "date":
            return await self._match_date_condition(operator, value, user_id)

        # Direct field conditions on douyin_videos
        query = self.supabase.table("douyin_videos").select("id").eq("user_id", user_id)

        field_mapping = {
            "author": "author",
            "title": "title",
            "description": "desc",
            "keep_forever": "keep_forever",
            "view_count": "view_count",
            "aweme_type": "aweme_type"
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

        result = query.execute()
        return [r["id"] for r in result.data]

    async def _match_tag_condition(self, operator: str, value: Any, user_id: str) -> List[int]:
        """Match videos by tag conditions."""
        # Get user's videos first
        user_videos = self.supabase.table("douyin_videos").select("id").eq("user_id", user_id).execute()
        user_video_ids = [v["id"] for v in user_videos.data]

        if not user_video_ids:
            return []

        if operator == "has":
            # Videos that have a specific tag
            result = self.supabase.table("video_tags").select("video_id").eq("tag_id", value).in_("video_id", user_video_ids).execute()
            return list(set(r["video_id"] for r in result.data))

        elif operator == "has_any":
            # Videos that have any of the specified tags
            if isinstance(value, list):
                result = self.supabase.table("video_tags").select("video_id").in_("tag_id", value).in_("video_id", user_video_ids).execute()
                return list(set(r["video_id"] for r in result.data))

        elif operator == "has_all":
            # Videos that have all of the specified tags
            if isinstance(value, list):
                video_tag_counts = {}
                result = self.supabase.table("video_tags").select("video_id").in_("tag_id", value).in_("video_id", user_video_ids).execute()
                for r in result.data:
                    vid = r["video_id"]
                    video_tag_counts[vid] = video_tag_counts.get(vid, 0) + 1
                return [vid for vid, count in video_tag_counts.items() if count == len(value)]

        return []

    async def _match_date_condition(self, operator: str, value: Any, user_id: str) -> List[int]:
        """Match videos by date conditions."""
        query = self.supabase.table("douyin_videos").select("id").eq("user_id", user_id)

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

        result = query.execute()
        return [r["id"] for r in result.data]

    async def _fetch_videos_by_ids(self, video_ids: List[int], collection: dict) -> List[dict]:
        """Fetch full video details for given IDs."""
        if not video_ids:
            return []

        sort_by = collection.get("sort_by", "created_at")
        sort_order = collection.get("sort_order", "desc")

        result = self.supabase.table("douyin_videos").select(
            "id, title, desc, author, cover_url, duration, aweme_type, created_at, view_count, keep_forever"
        ).in_("id", video_ids).order(sort_by, desc=(sort_order == "desc")).execute()

        return result.data

    async def refresh_collection_cache(self, collection_id: str, user_id: str) -> int:
        """Force refresh a collection's cache."""
        collection = await self.repo.get_collection_by_id(collection_id, user_id)
        if not collection:
            return 0

        rules = collection.get("rules", {})
        video_ids = await self._evaluate_rules(rules, user_id)
        total = len(video_ids)

        await self.repo.update_cache(collection_id, video_ids[:1000], total)

        logger.info(f"Refreshed cache for collection {collection_id}: {total} videos")
        return total
