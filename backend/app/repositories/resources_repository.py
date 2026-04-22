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

# MIME prefixes considered "known" — anything else is the catch-all "other".
_KNOWN_MIME_PREFIXES: tuple[str, ...] = (
    "video/",
    "image/",
    "audio/",
    "application/pdf",
    "application/msword",
    "application/vnd.",
    "text/",
)

# Broad resource-type categories → list of PostgREST filter clauses.
# Kept in sync with the frontend FilterType enum.
_TYPE_CATEGORY_CLAUSES: Dict[str, List[str]] = {
    "video": ["mime_type.like.video/*"],
    "image": ["mime_type.like.image/*"],
    "audio": ["mime_type.like.audio/*"],
    "document": [
        "mime_type.eq.application/pdf",
        "mime_type.like.application/msword*",
        "mime_type.like.application/vnd.*",
        "mime_type.like.text/*",
    ],
}


def _build_mime_or_expr(types: Optional[List[str]]) -> Optional[str]:
    """Translate a list of type categories into a PostgREST ``or=`` clause.

    Returns ``None`` when no filter is needed. Handles the ``other``
    category by building an AND-of-NOT expression against each known
    prefix; PostgREST expresses this as ``and(not.like.*, not.like.*, …)``
    inside the top-level ``or(…)`` group.
    """
    if not types:
        return None

    categories = {t.strip() for t in types if t and t.strip()}
    if not categories:
        return None

    clauses: List[str] = []
    for category in categories:
        if category in _TYPE_CATEGORY_CLAUSES:
            clauses.extend(_TYPE_CATEGORY_CLAUSES[category])
        elif category == "other":
            not_clauses = [
                (f"not.like.{p}*" if p.endswith("/") else f"not.like.{p}*")
                for p in _KNOWN_MIME_PREFIXES
            ]
            # Require the mime to not match ANY known prefix.
            and_expr = "and(" + ",".join(f"mime_type.{c}" for c in not_clauses) + ")"
            clauses.append(and_expr)
        # Silently ignore unknown categories; schema validation happens
        # in the router layer.

    if not clauses:
        return None
    return ",".join(clauses)


class ResourcesRepository:
    """Resource library data access (async)"""

    TABLE_RESOURCES = "resources"
    TABLE_ITEMS = "resource_items"
    TABLE_VERSIONS = "resource_versions"
    TABLE_FOLDERS = "folders"
    TABLE_RESOURCE_TAGS = "resource_tags"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

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

    async def get_resource_by_media_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("media_id", media_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource by media_id {media_id}: {e}")
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Look up resource by the external platform content ID (e.g. douyin aweme_id).

        Two-step: parsed_media.platform_id -> parsed_media.id -> resources.media_id
        """
        try:
            client = await self._get_client()
            # Step 1: find the media by platform_id
            media_result = (
                await client.table("parsed_media")
                .select("id")
                .eq("platform_id", platform_id)
                .limit(1)
                .execute()
            )
            if not media_result.data:
                return None
            media_uuid = media_result.data[0]["id"]
            # Step 2: find the resource by media_id
            return await self.get_resource_by_media_id(media_uuid)
        except Exception as e:
            logger.error(f"Failed to get resource by platform_id {platform_id}: {e}")
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get user's resource for a specific media item."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("media_id", media_id)
                .eq("creator_id", creator_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                f"Failed to get resource for media={media_id}, creator={creator_id}: {e}"
            )
            return None

    async def update_download_status(
        self, resource_id: str, statuses: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Update one or more download status fields on a resource.

        Args:
            resource_id: Resource ID.
            statuses: Dict of status fields, e.g. {"video_download_status": "completed"}.
        """
        valid_fields = {
            "video_download_status",
            "music_download_status",
            "cover_download_status",
            "image_download_status",
        }
        data = {k: v for k, v in statuses.items() if k in valid_fields}
        if not data:
            return None
        return await self.update_resource(resource_id, data)

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

    async def count_resources_by_media_id(self, media_id: str) -> int:
        """Count how many resources reference a given parsed_media ID."""
        try:
            client = await self._get_client()
            result = await (
                client.table(self.TABLE_RESOURCES)
                .select("id", count="exact")
                .eq("media_id", media_id)
                .execute()
            )
            return result.count or 0
        except Exception as e:
            logger.error(f"Failed to count resources for media {media_id}: {e}")
            return 0

    # ------------------------------------------------------------------ #
    # Hash-based duplicate lookup
    # ------------------------------------------------------------------ #

    async def find_by_hash(self, file_hash: str, creator_id: str) -> list[dict]:
        """Find non-trashed resources with the same file hash for a given creator."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select(
                    "id, filename, file_type, mime_type, file_size_bytes, "
                    "thumbnail_path, cover_image_path, created_at"
                )
                .eq("file_hash", file_hash)
                .eq("creator_id", creator_id)
                .eq("is_trashed", False)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []

    async def find_resource_item(
        self,
        resource_id: str,
        scope_type: str,
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        """Find a resource_item by resource_id + scope + folder."""
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
            )
            if folder_id:
                query = query.eq("folder_id", folder_id)
            else:
                query = query.is_("folder_id", "null")
            result = await query.limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to find resource_item: {e}")
            return None

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
        tag_ids: Optional[List[str]] = None,
        min_rating: Optional[int] = None,
        types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List resource_items joined to their resources.

        Optional filters:
        - ``tag_ids``: only items whose resource carries ALL of the given
          tag ids (AND semantics).
        - ``min_rating``: only items whose resource has ``rating`` >= value.
        - ``types``: broad type categories — ``video``, ``image``, ``audio``,
          ``document``, ``other`` — mapped against ``resource.mime_type``.
        """
        try:
            client = await self._get_client()

            # AND-semantic tag filter: compute the intersection of resource
            # ids tagged with every requested tag, then feed that set into
            # the main query. An empty intersection short-circuits to [].
            matched_resource_ids: Optional[List[str]] = None
            if tag_ids:
                matched_resource_ids = await self._resource_ids_with_all_tags(tag_ids)
                if not matched_resource_ids:
                    return []

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

            if matched_resource_ids is not None:
                query = query.in_("resource_id", matched_resource_ids)

            if min_rating is not None:
                query = query.gte("resource.rating", int(min_rating))

            mime_ors = _build_mime_or_expr(types)
            if mime_ors:
                # PostgREST `or=` on an embedded column requires the
                # `reference_table` kwarg so the parent parser doesn't
                # try to resolve the column against `resource_items`.
                query = query.or_(mime_ors, reference_table="resources")

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get resource items: {e}")
            return []

    async def _resource_ids_with_all_tags(self, tag_ids: List[str]) -> List[str]:
        """Return resource ids that carry every tag in ``tag_ids``.

        Implemented as N separate ``resource_tags`` lookups (one per tag)
        intersected in Python. Correct without relying on PostgREST
        group-by/having, which would require a dedicated RPC.
        """
        if not tag_ids:
            return []
        try:
            client = await self._get_client()
            result_set: Optional[set[str]] = None
            for tag_id in tag_ids:
                rows = (
                    await client.table(self.TABLE_RESOURCE_TAGS)
                    .select("resource_id")
                    .eq("tag_id", tag_id)
                    .execute()
                )
                ids = {str(r["resource_id"]) for r in (rows.data or [])}
                if result_set is None:
                    result_set = ids
                else:
                    result_set &= ids
                if not result_set:
                    return []
            return list(result_set or [])
        except Exception as e:
            logger.error(f"Failed to intersect resource tag ids: {e}")
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

    async def get_resource_item_in_folder(
        self, resource_id: str, scope_type: str, scope_id: str, folder_id: str | None
    ) -> Optional[Dict[str, Any]]:
        """Get a specific resource_item by resource_id + scope + folder_id."""
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
            )
            if folder_id:
                query = query.eq("folder_id", folder_id)
            else:
                query = query.is_("folder_id", "null")
            result = await query.limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource_item in folder: {e}")
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
            await client.table(self.TABLE_ITEMS).delete().eq("id", item_id).execute()
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

    async def get_version_by_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("id", version_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None

    async def get_version_by_number(
        self, resource_id: str, version_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("version_number", version_number)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                f"Failed to get version {version_number} for {resource_id}: {e}"
            )
            return None

    async def delete_version(self, version_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_VERSIONS)
                .delete()
                .eq("id", version_id)
                .execute()
            )
            logger.info(f"Deleted version {version_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete version {version_id}: {e}")
            raise

    async def update_version(
        self, version_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .update(data)
                .eq("id", version_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update version {version_id}: {e}")
            raise

    async def get_untranscoded_video_versions(self) -> List[Dict[str, Any]]:
        """Get video versions that have never been transcoded (NULL status, has file)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("id, resource_id, mime_type, file_path")
                .like("mime_type", "video/%")
                .is_("transcode_status", "null")
                .not_.is_("file_path", "null")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get untranscoded video versions: {e}")
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

    async def get_trashed_folders(
        self, scope_type: str, scope_id: str
    ) -> List[Dict[str, Any]]:
        """Get all trashed folders. Frontend handles root-level filtering."""
        try:
            client = await self._get_client()
            result = await (
                client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .eq("is_trashed", True)
                .order("trashed_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get trashed folders: {e}")
            return []

    async def restore_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Restore a folder, all descendant folders, and their resources."""
        restore_data = {"is_trashed": False, "trashed_at": None}
        restored_resources = 0
        restored_folders = 0

        try:
            client = await self._get_client()

            # Get all descendant trashed folders
            all_ids: List[str] = [folder_id]
            queue = [folder_id]
            while queue:
                parent_id = queue.pop(0)
                result = await (
                    client.table(self.TABLE_FOLDERS)
                    .select("id")
                    .eq("parent_id", parent_id)
                    .eq("is_trashed", True)
                    .execute()
                )
                for row in result.data or []:
                    child_id = str(row["id"])
                    all_ids.append(child_id)
                    queue.append(child_id)

            # 1. Restore resources in all affected folders
            for fid in all_ids:
                items_result = await (
                    client.table(self.TABLE_ITEMS)
                    .select("resource_id")
                    .eq("folder_id", fid)
                    .execute()
                )
                resource_ids = [
                    str(item["resource_id"]) for item in (items_result.data or [])
                ]
                for rid in resource_ids:
                    await (
                        client.table(self.TABLE_RESOURCES)
                        .update(restore_data)
                        .eq("id", rid)
                        .eq("is_trashed", True)
                        .execute()
                    )
                    restored_resources += 1

            # 2. Restore all folders
            for fid in all_ids:
                await (
                    client.table(self.TABLE_FOLDERS)
                    .update(restore_data)
                    .eq("id", fid)
                    .execute()
                )
                restored_folders += 1

            logger.info(
                f"Cascade-restored folder {folder_id}: "
                f"{restored_folders} folders, {restored_resources} resources"
            )
            return {
                "restored_folders": restored_folders,
                "restored_resources": restored_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-restore folder {folder_id}: {e}")
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

    async def get_descendant_folder_ids(self, folder_id: str) -> List[str]:
        """Recursively get all descendant folder IDs (children, grandchildren, etc.)."""
        all_ids: List[str] = []
        queue = [folder_id]
        try:
            client = await self._get_client()
            while queue:
                parent_id = queue.pop(0)
                result = await (
                    client.table(self.TABLE_FOLDERS)
                    .select("id")
                    .eq("parent_id", parent_id)
                    .eq("is_trashed", False)
                    .execute()
                )
                for row in result.data or []:
                    child_id = str(row["id"])
                    all_ids.append(child_id)
                    queue.append(child_id)
            return all_ids
        except Exception as e:
            logger.error(f"Failed to get descendant folders for {folder_id}: {e}")
            return []

    async def count_folder_contents(self, folder_ids: List[str]) -> Dict[str, int]:
        """Count resources and sub-folders within the given folder IDs."""
        try:
            client = await self._get_client()
            # Count resources via resource_items
            resource_count = 0
            for fid in folder_ids:
                result = await (
                    client.table(self.TABLE_ITEMS)
                    .select("id", count="exact")
                    .eq("folder_id", fid)
                    .execute()
                )
                resource_count += result.count or 0
            # Count sub-folders (excluding the root folder itself)
            subfolder_count = len(folder_ids) - 1 if len(folder_ids) > 1 else 0
            return {
                "resource_count": resource_count,
                "subfolder_count": subfolder_count,
            }
        except Exception as e:
            logger.error(f"Failed to count folder contents: {e}")
            return {"resource_count": 0, "subfolder_count": 0}

    async def trash_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Trash a folder, all descendant folders, and their resources."""
        now = datetime.now(timezone.utc).isoformat()
        trash_data = {"is_trashed": True, "trashed_at": now}

        descendant_ids = await self.get_descendant_folder_ids(folder_id)
        all_folder_ids = [folder_id] + descendant_ids

        trashed_resources = 0
        trashed_folders = 0

        try:
            client = await self._get_client()

            # 1. Trash resources in all affected folders
            for fid in all_folder_ids:
                # Get resource_items with location info
                items_result = await (
                    client.table(self.TABLE_ITEMS)
                    .select("resource_id, folder_id, library_id, scope_type, scope_id")
                    .eq("folder_id", fid)
                    .execute()
                )
                for item in items_result.data or []:
                    rid = str(item["resource_id"])
                    update_data = {
                        **trash_data,
                        "last_folder_id": item.get("folder_id"),
                        "last_library_id": item.get("library_id"),
                        "last_scope_type": item.get("scope_type"),
                        "last_scope_id": item.get("scope_id"),
                    }
                    await (
                        client.table(self.TABLE_RESOURCES)
                        .update(update_data)
                        .eq("id", rid)
                        .eq("is_trashed", False)
                        .execute()
                    )
                    trashed_resources += 1

            # 2. Trash all folders (descendants first, then root)
            for fid in reversed(all_folder_ids):
                await (
                    client.table(self.TABLE_FOLDERS)
                    .update(trash_data)
                    .eq("id", fid)
                    .execute()
                )
                trashed_folders += 1

            logger.info(
                f"Cascade-trashed folder {folder_id}: "
                f"{trashed_folders} folders, {trashed_resources} resources"
            )
            return {
                "trashed_folders": trashed_folders,
                "trashed_resources": trashed_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-trash folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_FOLDERS).delete().eq("id", folder_id).execute()
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

        return [item for item in items if matches_tags(item.get("resource_id", ""))]
