# app/repositories/resources_repository.py

"""
Resources Repository

Data access layer for the resource library: resources, resource_items,
resource_versions, and folders. Uses async Supabase admin client.
"""

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
            from datetime import datetime, timedelta, timezone

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
