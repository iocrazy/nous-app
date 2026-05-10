"""asyncpg + Supavisor implementation of ResourcesRepository.

Phase 3a — first hot-path migration after the agent_runs pilot. Scoped
to the 10 methods that hit the ``resources`` table directly. The
remaining surface (resource_items / resource_versions / folders / tags)
is left to Phase 3b/3c/3d so each PR stays reviewable.

Strategy: multiple inheritance from ``AsyncpgRepository`` and the
legacy ``ResourcesRepository``. Methods we override go through asyncpg;
unmigrated methods inherit the legacy supabase-py path. The factory in
``resources_repository.py`` returns this subclass when
``USE_ASYNCPG_RESOURCES`` is on, so call sites stay identical.

Why ``resources`` first inside the file:
  - Highest traffic surface (parse / download / dedup probes hit
    these 5+ times per fetch)
  - Simple shapes (single table, no jsonb columns) — no codec
    registration needed
  - L2 dedup probe ``get_completed_resource_by_url_and_creator``
    is the most-called supabase-py path under Bug C; replacing it
    cuts the worst hot spot

Behavioural parity vs legacy:
  - Same return shapes (dict | None, dict, list[dict], int, bool)
  - Same error handling (log + return None / [] / 0; raise on writes)
  - INFO log on every successful create/update/delete (matches legacy)
  - L2 dedup status detection mirrors legacy (image vs video status)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

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

    async def get_resource_by_id(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resources WHERE id = $1", resource_id
            )
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_media_id(
        self, media_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resources WHERE media_id = $1 LIMIT 1",
                media_id,
            )
        except Exception as e:
            logger.error(
                f"Failed to get resource by media_id {media_id}: {e}"
            )
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Two-step lookup parsed_media.platform_id → resources.media_id.

        Kept as two queries (rather than a JOIN) to match legacy shape
        — caller may already have the parsed_media row cached and
        collapsing into a JOIN would diverge the cache key."""
        try:
            media_uuid = await self.fetch_value(
                "SELECT id FROM parsed_media WHERE platform_id = $1 LIMIT 1",
                platform_id,
            )
            if not media_uuid:
                return None
            return await self.get_resource_by_media_id(str(media_uuid))
        except Exception as e:
            logger.error(
                f"Failed to get resource by platform_id {platform_id}: {e}"
            )
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resources "
                "WHERE media_id = $1 AND creator_id = $2 LIMIT 1",
                media_id, creator_id,
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
                creator_id, url,
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
            row = await self.update_by_id(resource_id, **data)
            logger.info(f"Updated resource {resource_id}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        try:
            await self.execute(
                "DELETE FROM resources WHERE id = $1", resource_id
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
                media_id,
            )
            return int(count or 0)
        except Exception as e:
            logger.error(
                f"Failed to count resources for media {media_id}: {e}"
            )
            return 0

    # ── Hash-based duplicate lookup ─────────────────────────────────

    async def find_by_hash(
        self, file_hash: str, creator_id: str
    ) -> list[dict]:
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
                file_hash, creator_id,
            )
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []


__all__ = ["ResourcesRepositoryAsyncpg"]
