"""asyncpg + Supavisor implementation of ResourcesRepository.

Migration of the legacy supabase-py ``ResourcesRepository`` to direct
asyncpg + Supavisor. Lands in phases so each PR stays reviewable:

  - Phase 3a: ``resources`` table core (10 methods) — shipped #213
  - Phase 3b: ``resource_items`` table (12 methods) — this file
  - Phase 3c: ``resource_versions`` table — pending
  - Phase 3d: ``folders`` table — pending

Strategy: multiple inheritance from ``AsyncpgRepository`` and the
legacy ``ResourcesRepository``. Methods we override go through asyncpg;
unmigrated methods inherit the legacy supabase-py path via MRO. The
factory in ``resources_repository.py`` returns this subclass when
``USE_ASYNCPG_RESOURCES`` is on, so call sites stay identical.

Behavioural parity vs legacy:
  - Same return shapes (dict | None, dict, list[dict], int, bool)
  - Same error handling (log + return None / [] / 0; raise on writes)
  - INFO log on every successful create/update/delete (matches legacy)
  - L2 dedup status detection mirrors legacy (image vs video status)
  - NULL folder_id handled identically (``IS NULL`` vs ``= $1``)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.repository_base import AsyncpgRepository
from app.repositories.resources_repository import ResourcesRepository


class ResourcesRepositoryAsyncpg(AsyncpgRepository, ResourcesRepository):
    """asyncpg-backed ResourcesRepository for the ``resources`` table.

    Overrides the 10 methods that touch ``resources`` directly. All
    other methods (resource_items / versions / folders) fall through
    to the legacy supabase-py implementations on
    ``ResourcesRepository``."""

    TABLE = "resources"

    # ── Resources CRUD ──────────────────────────────────────────────

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            row = await self.insert(**data)
            logger.info(f"Created resource: {data.get('filename')}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to create resource: {e}")
            raise

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resources WHERE id = $1",
                self._bigint(resource_id),
            )
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_media_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resources WHERE media_id = $1 LIMIT 1",
                self._bigint(media_id),
            )
        except Exception as e:
            logger.error(f"Failed to get resource by media_id {media_id}: {e}")
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Two-step lookup parsed_media.platform_id → resources.media_id.

        Kept as two queries (rather than a JOIN) to match legacy shape
        — caller may already have the parsed_media row cached and
        collapsing into a JOIN would diverge the cache key."""
        try:
            media_id = await self.fetch_value(
                "SELECT id FROM parsed_media WHERE platform_id = $1 LIMIT 1",
                platform_id,
            )
            if not media_id:
                return None
            # Pass the int through directly. parsed_media.id and
            # resources.media_id are both BIGINT (Snowflake, migration
            # 051); asyncpg binds str→bigint as a hard error. The
            # legacy variable name "media_uuid" was misleading — it's
            # always been a Snowflake int since the migration.
            return await self.get_resource_by_media_id(media_id)
        except Exception as e:
            logger.error(f"Failed to get resource by platform_id {platform_id}: {e}")
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resources "
                "WHERE media_id = $1 AND creator_id = $2 LIMIT 1",
                self._bigint(media_id),
                creator_id,
            )
        except Exception as e:
            logger.error(
                f"Failed to get resource for media={media_id}, "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_completed_resource_by_url_and_creator(
        self, url: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """L2 dedup probe — most-called supabase-py path. JOIN with
        parsed_media on media_id, filter on original_url and the
        appropriate completion status (image vs video).

        Returns the same nested shape as legacy: ``{id, media_id,
        parsed_media: {id, platform_id, original_url, ...}}`` so call
        sites read the inner dict the same way."""
        try:
            row = await self.fetch_one(
                "SELECT r.id AS r_id, r.media_id AS r_media_id, "
                "       p.id AS p_id, p.platform_id, p.original_url, "
                "       p.video_download_status, p.image_download_status, "
                "       p.media_type "
                "FROM resources r "
                "INNER JOIN parsed_media p ON r.media_id = p.id "
                "WHERE r.creator_id = $1 AND p.original_url = $2 "
                "LIMIT 1",
                creator_id,
                url,
            )
            if not row:
                return None

            mt = row.get("media_type")
            is_image = str(mt) in ("2", "68", "image", "images")
            status = (
                row.get("image_download_status")
                if is_image
                else row.get("video_download_status")
            )
            if status != "completed":
                return None

            # Reshape into the nested dict the legacy contract returns.
            return {
                "id": row["r_id"],
                "media_id": row["r_media_id"],
                "parsed_media": {
                    "id": row["p_id"],
                    "platform_id": row["platform_id"],
                    "original_url": row["original_url"],
                    "video_download_status": row["video_download_status"],
                    "image_download_status": row["image_download_status"],
                    "media_type": row["media_type"],
                },
            }
        except Exception as e:
            logger.debug(
                f"[ResourcesRepo] L2 dedup probe failed url={url[:40]} "
                f"creator={creator_id}: {e}"
            )
            return None

    async def update_resource(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            row = await self.update_by_id(self._bigint(resource_id), **data)
            logger.info(f"Updated resource {resource_id}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        try:
            await self.execute(
                "DELETE FROM resources WHERE id = $1",
                self._bigint(resource_id),
            )
            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            raise

    async def count_resources_by_media_id(self, media_id: str) -> int:
        try:
            count = await self.fetch_value(
                "SELECT count(*) FROM resources WHERE media_id = $1",
                self._bigint(media_id),
            )
            return int(count or 0)
        except Exception as e:
            logger.error(f"Failed to count resources for media {media_id}: {e}")
            return 0

    # ── Hash-based duplicate lookup ─────────────────────────────────

    async def find_by_hash(self, file_hash: str, creator_id: str) -> list[dict]:
        """Find non-trashed resources with the same file hash for a
        given creator. Column projection matches legacy exactly so
        callers don't accidentally start depending on extra fields."""
        try:
            return await self.fetch_all(
                "SELECT id, filename, file_type, mime_type, "
                "       file_size_bytes, thumbnail_path, "
                "       cover_image_path, created_at "
                "FROM resources "
                "WHERE file_hash = $1 "
                "  AND creator_id = $2 "
                "  AND is_trashed = false",
                file_hash,
                creator_id,
            )
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []

    # ── Resource Items ──────────────────────────────────────────────
    #
    # ``resource_items`` is the workspace-scope/folder routing layer
    # between a ``resources`` row and a ``(scope_type, scope_id,
    # folder_id)`` location. NULL ``folder_id`` represents the root
    # of a scope; we use ``IS NULL`` rather than ``= $N`` so the
    # query plan is the same as the legacy ``query.is_("folder_id",
    # "null")`` call — a parameter would force a nullable comparison
    # that never matches.

    async def find_resource_item(
        self,
        resource_id: str,
        scope_type: str,
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        try:
            if folder_id:
                return await self.fetch_one(
                    "SELECT * FROM resource_items "
                    "WHERE resource_id = $1 "
                    "  AND scope_type = $2 "
                    "  AND scope_id = $3 "
                    "  AND folder_id = $4 "
                    "LIMIT 1",
                    self._bigint(resource_id),
                    scope_type,
                    scope_id,
                    self._bigint(folder_id),
                )
            return await self.fetch_one(
                "SELECT * FROM resource_items "
                "WHERE resource_id = $1 "
                "  AND scope_type = $2 "
                "  AND scope_id = $3 "
                "  AND folder_id IS NULL "
                "LIMIT 1",
                self._bigint(resource_id),
                scope_type,
                scope_id,
            )
        except Exception as e:
            logger.error(f"Failed to find resource_item: {e}")
            return None

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            cols = list(data.keys())
            placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
            col_list = ", ".join(f'"{c}"' for c in cols)
            sql = (
                f'INSERT INTO "resource_items" ({col_list}) '
                f"VALUES ({placeholders}) RETURNING *"
            )
            row = await self.fetch_one(sql, *data.values())
            logger.info(
                f"Created resource_item for resource {data.get('resource_id')} "
                f"in {data.get('scope_type')}/{data.get('scope_id')}"
            )
            return row or {}
        except Exception as e:
            logger.error(f"Failed to create resource_item: {e}")
            raise

    async def _resource_ids_for_platforms(self, platforms: List[str]) -> List[str]:
        """Resource ids whose linked parsed_media.source_platform is in
        ``platforms``. Two-step lookup retained for parity (the legacy
        version avoided a nested PostgREST `in` filter; we keep the
        same shape so call-site cache keys don't shift).

        Returns list[str] (not list[int]) because the inherited
        ``get_resource_items`` legacy method passes this output to
        PostgREST ``query.in_("resource_id", matched)`` which expects
        strings."""
        cleaned = [p.strip() for p in platforms if p and p.strip()]
        if not cleaned:
            return []
        try:
            media_rows = await self.fetch_all(
                "SELECT id FROM parsed_media "
                "WHERE source_platform = ANY($1::text[])",
                cleaned,
            )
            # Keep ints internally — bigint comparison below needs int.
            media_ids_int = [row["id"] for row in media_rows]
            if not media_ids_int:
                return []
            resource_rows = await self.fetch_all(
                "SELECT id FROM resources " "WHERE media_id = ANY($1::bigint[])",
                media_ids_int,
            )
            # Stringify on the way out for the legacy caller contract.
            return [str(row["id"]) for row in resource_rows]
        except Exception as e:
            logger.error(f"Failed to resolve resource ids for platforms: {e}")
            return []

    async def _resource_ids_with_all_tags(self, tag_ids: List[str]) -> List[str]:
        """Resource ids that carry EVERY tag in ``tag_ids`` (AND).

        Single-query GROUP BY HAVING count = N is correct here and
        cheaper than the N round-trips the legacy version does in
        Python — kept compatible with empty-input fast path.

        ``tag_id`` is uuid; ``$1::uuid[]`` lets PG cast the array of
        str values to uuid for the comparison. text[] would error
        against the uuid column in strict-typed PG."""
        if not tag_ids:
            return []
        try:
            rows = await self.fetch_all(
                "SELECT resource_id FROM resource_tags "
                "WHERE tag_id = ANY($1::uuid[]) "
                "GROUP BY resource_id "
                "HAVING count(DISTINCT tag_id) = $2",
                tag_ids,
                len(set(tag_ids)),
            )
            return [str(r["resource_id"]) for r in rows]
        except Exception as e:
            logger.error(f"Failed to intersect resource tag ids: {e}")
            return []

    async def get_resource_item(
        self, resource_id: str, scope_type: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resource_items "
                "WHERE resource_id = $1 "
                "  AND scope_type = $2 "
                "  AND scope_id = $3 "
                "LIMIT 1",
                self._bigint(resource_id),
                scope_type,
                scope_id,
            )
        except Exception as e:
            logger.error(f"Failed to get resource_item: {e}")
            return None

    async def get_resource_item_in_folder(
        self,
        resource_id: str,
        scope_type: str,
        scope_id: str,
        folder_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        try:
            if folder_id:
                return await self.fetch_one(
                    "SELECT * FROM resource_items "
                    "WHERE resource_id = $1 "
                    "  AND scope_type = $2 "
                    "  AND scope_id = $3 "
                    "  AND folder_id = $4 "
                    "LIMIT 1",
                    self._bigint(resource_id),
                    scope_type,
                    scope_id,
                    self._bigint(folder_id),
                )
            return await self.fetch_one(
                "SELECT * FROM resource_items "
                "WHERE resource_id = $1 "
                "  AND scope_type = $2 "
                "  AND scope_id = $3 "
                "  AND folder_id IS NULL "
                "LIMIT 1",
                self._bigint(resource_id),
                scope_type,
                scope_id,
            )
        except Exception as e:
            logger.error(f"Failed to get resource_item in folder: {e}")
            return None

    async def get_first_resource_item(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resource_items " "WHERE resource_id = $1 " "LIMIT 1",
                self._bigint(resource_id),
            )
        except Exception as e:
            logger.error(f"Failed to get first resource_item: {e}")
            return None

    async def update_resource_item(
        self, item_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            cols = list(data.keys())
            set_pairs = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(cols))
            sql = (
                f'UPDATE "resource_items" SET {set_pairs} '
                f"WHERE id = ${len(cols) + 1} RETURNING *"
            )
            row = await self.fetch_one(sql, *data.values(), self._bigint(item_id))
            return row or {}
        except Exception as e:
            logger.error(f"Failed to update resource_item {item_id}: {e}")
            raise

    async def delete_resource_item(self, item_id: str) -> bool:
        """Delete a resource_item by ID. The DB trigger auto-trashes
        the parent resource if this was the last reference."""
        try:
            await self.execute(
                "DELETE FROM resource_items WHERE id = $1",
                self._bigint(item_id),
            )
            logger.info(f"Deleted resource_item {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource_item {item_id}: {e}")
            raise

    async def count_resource_items(self, resource_id: str) -> int:
        try:
            count = await self.fetch_value(
                "SELECT count(*) FROM resource_items WHERE resource_id = $1",
                self._bigint(resource_id),
            )
            return int(count or 0)
        except Exception as e:
            logger.error(f"Failed to count items for resource {resource_id}: {e}")
            return 0

    # ── Trash listing ───────────────────────────────────────────────

    async def get_expired_trashed_resources(
        self, older_than_days: int = 30
    ) -> List[Dict[str, Any]]:
        """Trashed resources older than N days, for permanent cleanup."""
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=older_than_days)
            ).isoformat()
            return await self.fetch_all(
                "SELECT id, file_path, cover_image_path FROM resources "
                "WHERE is_trashed = true AND trashed_at < $1",
                cutoff,
            )
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: str, scope_id: str
    ) -> List[Dict[str, Any]]:
        """Trashed resources in a scope. JOIN with resources, re-shape
        flat row → ``{item_columns..., resource: {...}}`` to match the
        embedded-PostgREST shape callers depend on.

        ``row_to_json(r.*)`` is decoded explicitly because asyncpg's
        default json codec returns a str, not a dict — callers expect
        a dict so the embedded-PostgREST shape is preserved."""
        import json

        try:
            rows = await self.fetch_all(
                "SELECT i.id, i.resource_id, i.scope_type, i.scope_id, "
                "       i.folder_id, i.created_at AS i_created_at, "
                "       i.updated_at AS i_updated_at, "
                "       row_to_json(r.*) AS resource "
                "FROM resource_items i "
                "INNER JOIN resources r ON i.resource_id = r.id "
                "WHERE i.scope_type = $1 "
                "  AND i.scope_id = $2 "
                "  AND r.is_trashed = true "
                "ORDER BY i.created_at DESC",
                scope_type,
                scope_id,
            )
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                row["created_at"] = row.pop("i_created_at")
                row["updated_at"] = row.pop("i_updated_at")
            return rows
        except Exception as e:
            logger.error(f"Failed to get trashed resources: {e}")
            return []


__all__ = ["ResourcesRepositoryAsyncpg"]
