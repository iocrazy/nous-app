"""Smart Collections service with rule engine (异步)."""

from datetime import datetime, timedelta
from typing import Any, Dict, List
from uuid import UUID

from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope
from app.repositories._orm_helpers import _plain
from app.repositories.collections_repository import get_collections_repository


def _serialize_media_row(row: Any) -> Dict[str, Any]:
    """parsed_media card row → JSON-safe dict (enum→str, ts→iso, uuid→str),
    matching the PostgREST shape the collection media list returned."""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        value = _plain(value)
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


class CollectionsService:
    """Service for smart collection rule evaluation and media matching (异步)."""

    def __init__(self):
        self.repo = get_collections_repository()

    async def _get_user_resource_mapping(self, user_id: str) -> Dict[int, int]:
        """Get user's resource mapping {resource_id: media_id} from resources table.

        Since parsed_media is now a global table (no user_id column),
        we identify the user's media through the resources table.
        """
        from app.models import Resources

        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(Resources.id, Resources.media_id)
                    .where(Resources.creator_id == user_id)
                    .where(Resources.source_type == "web")
                    .where(Resources.is_trashed.is_(False))
                )
            ).all()
        return {r[0]: r[1] for r in rows if r[1] is not None}

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
            if cache_age < timedelta(minutes=5) and collection.get("cached_video_ids"):
                # Use cached media IDs
                media_ids = collection["cached_video_ids"]
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
        from app.models import ParsedMedia

        # Tag-based conditions
        if field == "tag":
            return await self._match_tag_condition(operator, value, resource_map)

        # Date-based conditions
        if field == "date":
            return await self._match_date_condition(operator, value, user_media_ids)

        field_mapping = {
            "author": "author",
            "title": "title",
            "description": "description",
            "keep_forever": "keep_forever",
            "view_count": "view_count",
            "media_type": "media_type",
        }
        db_field = field_mapping.get(field, field)
        col = getattr(ParsedMedia, db_field)

        # Direct field conditions on parsed_media (scoped to user's media)
        stmt = select(ParsedMedia.id).where(ParsedMedia.id.in_(user_media_ids))
        if operator == "equals":
            stmt = stmt.where(col == value)
        elif operator == "contains":
            stmt = stmt.where(col.ilike(f"%{value}%"))
        elif operator == "starts_with":
            stmt = stmt.where(col.ilike(f"{value}%"))
        elif operator == "gt":
            stmt = stmt.where(col > value)
        elif operator == "gte":
            stmt = stmt.where(col >= value)
        elif operator == "lt":
            stmt = stmt.where(col < value)
        elif operator == "lte":
            stmt = stmt.where(col <= value)
        elif operator == "in":
            if isinstance(value, list):
                stmt = stmt.where(col.in_(value))

        async with read_scope() as session:
            rows = (await session.execute(stmt)).all()
        return [r[0] for r in rows]

    async def _match_tag_condition(
        self, operator: str, value: Any, resource_map: Dict[int, int]
    ) -> List[int]:
        """Match media by tag conditions.

        resource_tags.resource_id references resources.id,
        so we query by user's resource IDs and map back to media IDs.
        """
        from app.models import ResourceTags

        resource_ids = list(resource_map.keys())
        if not resource_ids:
            return []

        async def _matching_resource_ids(tag_filter) -> List[int]:
            # resource_tags.tag_id is BIGINT (mig 078 UUID→BIGINT) → bind int.
            async with read_scope() as session:
                rows = (
                    await session.execute(
                        select(ResourceTags.resource_id)
                        .where(tag_filter)
                        .where(ResourceTags.resource_id.in_(resource_ids))
                    )
                ).all()
            return [r[0] for r in rows]

        if operator == "has":
            # Media that have a specific tag
            rids = await _matching_resource_ids(ResourceTags.tag_id == int(value))
            # Map resource_id back to media_id
            return list({resource_map[rid] for rid in rids if rid in resource_map})

        elif operator == "has_any":
            # Media that have any of the specified tags
            if isinstance(value, list):
                rids = await _matching_resource_ids(
                    ResourceTags.tag_id.in_([int(v) for v in value])
                )
                return list({resource_map[rid] for rid in rids if rid in resource_map})

        elif operator == "has_all":
            # Media that have all of the specified tags
            if isinstance(value, list):
                media_tag_counts: Dict[int, int] = {}
                rids = await _matching_resource_ids(
                    ResourceTags.tag_id.in_([int(v) for v in value])
                )
                for rid in rids:
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
        from app.models import ParsedMedia

        if not user_media_ids:
            return []

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

        # created_at is timestamptz → bind a native datetime (asyncpg rejects
        # the ISO string PostgREST used to coerce).
        if isinstance(value, str):
            value = datetime.fromisoformat(value)

        stmt = select(ParsedMedia.id).where(ParsedMedia.id.in_(user_media_ids))
        if operator == "gte":
            stmt = stmt.where(ParsedMedia.created_at >= value)
        elif operator == "lte":
            stmt = stmt.where(ParsedMedia.created_at <= value)
        elif operator == "gt":
            stmt = stmt.where(ParsedMedia.created_at > value)
        elif operator == "lt":
            stmt = stmt.where(ParsedMedia.created_at < value)

        async with read_scope() as session:
            rows = (await session.execute(stmt)).all()
        return [r[0] for r in rows]

    async def _fetch_media_by_ids(
        self, media_ids: List[int], collection: dict
    ) -> List[dict]:
        """Fetch full media details for given IDs."""
        if not media_ids:
            return []

        from app.models import ParsedMedia

        sort_by = collection.get("sort_by", "created_at")
        sort_order = collection.get("sort_order", "desc")
        sort_col = getattr(ParsedMedia, sort_by)
        order = sort_col.desc() if sort_order == "desc" else sort_col.asc()

        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            ParsedMedia.id,
                            ParsedMedia.title,
                            ParsedMedia.description,
                            ParsedMedia.author,
                            ParsedMedia.cover_urls,
                            ParsedMedia.duration,
                            ParsedMedia.media_type,
                            ParsedMedia.created_at,
                            ParsedMedia.view_count,
                            ParsedMedia.keep_forever,
                        )
                        .where(ParsedMedia.id.in_(media_ids))
                        .order_by(order)
                    )
                )
                .mappings()
                .all()
            )
        return [_serialize_media_row(r) for r in rows]

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
