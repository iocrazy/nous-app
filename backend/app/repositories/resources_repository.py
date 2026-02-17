# app/repositories/resources_repository.py

"""
Resources Repository

Data access layer for the resource library: resources, resource_items,
resource_versions, and folders. Uses async Supabase admin client.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class ResourcesRepository:
    """Resource library data access (async)"""

    TABLE_RESOURCES = "resources"
    TABLE_ITEMS = "resource_items"
    TABLE_VERSIONS = "resource_versions"
    TABLE_FOLDERS = "folders"
    TABLE_RESOURCE_TAGS = "resource_tags"

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    # ------------------------------------------------------------------ #
    # Resources CRUD
    # ------------------------------------------------------------------ #

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_RESOURCES).insert(data).execute()
            logger.info(f"Created resource: {data.get('filename')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create resource: {e}")
            raise

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("id", resource_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_video_id(self, video_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("video_id", video_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource by video_id {video_id}: {e}")
            return None

    async def update_resource(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .update(data)
                .eq("id", resource_id)
                .execute()
            )
            logger.info(f"Updated resource {resource_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_RESOURCES)
                .delete()
                .eq("id", resource_id)
                .execute()
            )
            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Resource Items (workspace scoping)
    # ------------------------------------------------------------------ #

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_ITEMS).insert(data).execute()
            logger.info(
                f"Created resource_item for resource {data.get('resource_id')} "
                f"in {data.get('scope_type')}/{data.get('scope_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create resource_item: {e}")
            raise

    async def get_resource_items(
        self,
        scope_type: str,
        scope_id: str,
        folder_id: Optional[str] = None,
        include_trashed: bool = False,
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*, resource:resources!inner(*)")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
            )
            if folder_id:
                query = query.eq("folder_id", folder_id)
            else:
                query = query.is_("folder_id", "null")

            if not include_trashed:
                query = query.eq("resource.is_trashed", False)

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get resource items: {e}")
            return []

    async def get_resource_item(
        self, resource_id: str, scope_type: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource_item: {e}")
            return None

    async def get_first_resource_item(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the first resource_item for a resource (any scope)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get first resource_item: {e}")
            return None

    async def update_resource_item(
        self, item_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .update(data)
                .eq("id", item_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update resource_item {item_id}: {e}")
            raise

    async def delete_resource_item(self, item_id: str) -> bool:
        """Delete a resource_item by ID. The DB trigger will auto-trash
        the parent resource if this was the last reference."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_ITEMS)
                .delete()
                .eq("id", item_id)
                .execute()
            )
            logger.info(f"Deleted resource_item {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource_item {item_id}: {e}")
            raise

    async def count_resource_items(self, resource_id: str) -> int:
        """Count how many resource_items reference a given resource."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("id", count="exact")
                .eq("resource_id", resource_id)
                .execute()
            )
            return result.count or 0
        except Exception as e:
            logger.error(f"Failed to count items for resource {resource_id}: {e}")
            return 0

    async def get_expired_trashed_resources(
        self, older_than_days: int = 30
    ) -> List[Dict[str, Any]]:
        """Find trashed resources older than N days for permanent cleanup."""
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=older_than_days)
            ).isoformat()
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("id, file_path, cover_image_path")
                .eq("is_trashed", True)
                .lt("trashed_at", cutoff)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: str, scope_id: str
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("*, resource:resources!inner(*)")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .eq("resource.is_trashed", True)
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get trashed resources: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Resource Versions
    # ------------------------------------------------------------------ #

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_VERSIONS).insert(data).execute()
            logger.info(
                f"Created version {data.get('version_number')} "
                f"for resource {data.get('resource_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    async def get_versions(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("resource_id", resource_id)
                .order("version_number", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get versions for resource {resource_id}: {e}")
            return []

    async def get_next_version_number(self, resource_id: str) -> int:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("version_number")
                .eq("resource_id", resource_id)
                .order("version_number", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["version_number"] + 1
            return 1
        except Exception as e:
            logger.error(f"Failed to get next version for resource {resource_id}: {e}")
            return 1

    # ------------------------------------------------------------------ #
    # Folders CRUD
    # ------------------------------------------------------------------ #

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_FOLDERS).insert(data).execute()
            logger.info(f"Created folder: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_folders(
        self, scope_type: str, scope_id: str, include_trashed: bool = False
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
            )
            if not include_trashed:
                query = query.eq("is_trashed", False)
            query = query.order("sort_order", desc=False)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get folders: {e}")
            return []

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("id", folder_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def update_folder(
        self, folder_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .update(data)
                .eq("id", folder_id)
                .execute()
            )
            logger.info(f"Updated folder {folder_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_FOLDERS)
                .delete()
                .eq("id", folder_id)
                .execute()
            )
            logger.info(f"Deleted folder {folder_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Resource Tags
    # ------------------------------------------------------------------ #

    async def add_resource_tag(
        self, resource_id: str, tag_id: str, tagged_by: str
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCE_TAGS)
                .insert(
                    {
                        "resource_id": resource_id,
                        "tag_id": tag_id,
                        "tagged_by": tagged_by,
                    }
                )
                .execute()
            )
            logger.info(f"Tagged resource {resource_id} with tag {tag_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to tag resource {resource_id}: {e}")
            raise

    async def remove_resource_tag(self, resource_id: str, tag_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_RESOURCE_TAGS)
                .delete()
                .eq("resource_id", resource_id)
                .eq("tag_id", tag_id)
                .execute()
            )
            logger.info(f"Removed tag {tag_id} from resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove tag from resource {resource_id}: {e}")
            raise

    async def get_resource_tags(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCE_TAGS)
                .select("*, tag:tags(*)")
                .eq("resource_id", resource_id)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get tags for resource {resource_id}: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Smart Folders
    # ------------------------------------------------------------------ #

    async def get_smart_folders(
        self, scope_type: str, scope_id: str
    ) -> List[Dict[str, Any]]:
        """Get all smart folders for a scope."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .eq("is_smart", True)
                .eq("is_trashed", False)
                .order("sort_order", desc=False)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get smart folders: {e}")
            return []

    async def create_smart_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a folder with is_smart=true."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_FOLDERS).insert(data).execute()
            logger.info(f"Created smart folder: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create smart folder: {e}")
            raise

    async def execute_smart_rules(
        self, scope_type: str, scope_id: str, rules: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Execute smart folder rules against resource_items + resources.

        Build a Supabase PostgREST query from the JSONB rules:
        - For fields on resources table: use resource.{field} in the join
        - For tags: use a subquery on resource_tags
        - For relative dates: compute the absolute date
        - Apply AND/OR logic
        - Apply match/exclude logic
        """
        try:
            client = await self._get_client()
            conditions = rules.get("conditions", [])
            operator = rules.get("operator", "AND")
            match = rules.get("match", True)

            # Separate tag conditions from resource conditions
            tag_conditions = [c for c in conditions if c["field"] == "tags"]
            resource_conditions = [c for c in conditions if c["field"] != "tags"]

            # Base query: resource_items with joined resources
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*, resource:resources!inner(*)")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .eq("resource.is_trashed", False)
            )

            if operator == "AND":
                # Apply each resource condition as a filter
                for cond in resource_conditions:
                    query = self._apply_condition(query, cond)
            else:
                # OR: use .or_() with PostgREST format
                if resource_conditions:
                    or_parts = []
                    for cond in resource_conditions:
                        part = self._condition_to_postgrest(cond)
                        if part:
                            or_parts.append(part)
                    if or_parts:
                        query = query.or_(
                            ",".join(or_parts), reference_table="resources"
                        )

            result = await query.order("created_at", desc=True).execute()
            items = result.data or []

            # Post-filter for tag conditions (tags live in resource_tags table)
            if tag_conditions:
                items = await self._filter_by_tags(
                    items, tag_conditions, operator, client
                )

            # Apply match/exclude logic
            if not match:
                # Exclude mode: get ALL items and subtract the matched set
                all_query = (
                    client.table(self.TABLE_ITEMS)
                    .select("*, resource:resources!inner(*)")
                    .eq("scope_type", scope_type)
                    .eq("scope_id", scope_id)
                    .eq("resource.is_trashed", False)
                    .order("created_at", desc=True)
                )
                all_result = await all_query.execute()
                all_items = all_result.data or []
                matched_ids = {item["id"] for item in items}
                items = [item for item in all_items if item["id"] not in matched_ids]

            return items
        except Exception as e:
            logger.error(f"Failed to execute smart rules: {e}")
            return []

    def _apply_condition(self, query, cond: Dict[str, Any]):
        """Apply a single condition as a PostgREST filter (AND mode)."""
        field = cond["field"]
        op = cond["op"]
        value = self._resolve_value(cond["value"])

        col = f"resource.{field}"

        if op == "eq":
            return query.eq(col, value)
        elif op == "contains":
            return query.ilike(col, f"%{value}%")
        elif op == "starts_with":
            return query.ilike(col, f"{value}%")
        elif op == "gt":
            return query.gt(col, value)
        elif op == "lt":
            return query.lt(col, value)
        elif op == "gte":
            return query.gte(col, value)
        elif op == "lte":
            return query.lte(col, value)
        elif op == "in":
            return query.in_(col, value.split(","))
        return query

    def _condition_to_postgrest(self, cond: Dict[str, Any]) -> Optional[str]:
        """Convert a condition to PostgREST OR filter string."""
        field = cond["field"]
        op = cond["op"]
        value = self._resolve_value(cond["value"])

        if op == "eq":
            return f"{field}.eq.{value}"
        elif op == "contains":
            return f"{field}.ilike.%{value}%"
        elif op == "starts_with":
            return f"{field}.ilike.{value}%"
        elif op == "gt":
            return f"{field}.gt.{value}"
        elif op == "lt":
            return f"{field}.lt.{value}"
        elif op == "gte":
            return f"{field}.gte.{value}"
        elif op == "lte":
            return f"{field}.lte.{value}"
        elif op == "in":
            vals = value.replace(",", '","')
            return f'{field}.in.("{vals}")'
        return None

    def _resolve_value(self, value: str) -> str:
        """Resolve relative dates like 'relative:-7d' to absolute ISO dates."""
        if isinstance(value, str) and value.startswith("relative:"):
            offset_str = value.split(":")[1]
            # Parse -7d, -30d, -1h, etc.
            unit = offset_str[-1]
            amount = int(offset_str[:-1])
            now = datetime.now(timezone.utc)
            if unit == "d":
                target = now + timedelta(days=amount)
            elif unit == "h":
                target = now + timedelta(hours=amount)
            elif unit == "m":
                target = now + timedelta(minutes=amount)
            else:
                target = now + timedelta(days=amount)
            return target.isoformat()
        return value

    async def _filter_by_tags(
        self,
        items: List[Dict[str, Any]],
        tag_conditions: List[Dict[str, Any]],
        operator: str,
        client,
    ) -> List[Dict[str, Any]]:
        """Post-filter items by tag conditions using resource_tags table."""
        if not items:
            return items

        # Get resource IDs from items
        resource_ids = list(
            {item.get("resource_id") for item in items if item.get("resource_id")}
        )
        if not resource_ids:
            return []

        # Fetch all tags for these resources
        tag_result = (
            await client.table(self.TABLE_RESOURCE_TAGS)
            .select("resource_id, tag:tags(name)")
            .in_("resource_id", resource_ids)
            .execute()
        )
        tag_data = tag_result.data or []

        # Build resource_id -> set of tag names
        resource_tags: Dict[str, set] = {}
        for row in tag_data:
            rid = row["resource_id"]
            tag_name = row.get("tag", {}).get("name", "")
            if rid not in resource_tags:
                resource_tags[rid] = set()
            resource_tags[rid].add(tag_name.lower())

        # Apply tag conditions
        def matches_tags(resource_id: str) -> bool:
            tags = resource_tags.get(resource_id, set())
            results = []
            for cond in tag_conditions:
                tag_value = cond["value"].lower()
                if cond["op"] == "contains":
                    results.append(tag_value in tags)
                elif cond["op"] == "not_contains":
                    results.append(tag_value not in tags)
                else:
                    results.append(False)
            if operator == "AND":
                return all(results)
            return any(results)

        return [
            item
            for item in items
            if matches_tags(item.get("resource_id", ""))
        ]
