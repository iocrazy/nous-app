"""asyncpg + Supavisor implementation of ResourcesRepository.

Migration of the legacy supabase-py ``ResourcesRepository`` to direct
asyncpg + Supavisor. Lands in phases so each PR stays reviewable:

  - Phase 3a: ``resources`` table core (10 methods) — shipped #213
  - Phase 3b: ``resource_items`` table (12 methods) — shipped #219
  - Phase 3c: ``resource_versions`` table (8 methods) — shipped #220
  - Phase 3d: ``folders`` table (10 methods) — shipped #221
  - Phase 3e: ``get_resource_items`` (1 method, deferred from 3b) — this file

After 3e the only remaining legacy-path methods on this file are
``add_resource_tag`` / ``remove_resource_tag`` / ``get_resource_tags``
(resource_tags surface) and the smart-folder trio
(``get_smart_folders`` / ``create_smart_folder`` /
``execute_smart_rules``). Those land in Phase 3f if/when needed.

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

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.repository_base import AsyncpgRepository
from app.repositories.resources_repository import (
    _AI_STATUS_COMPLETED,
    _AI_STATUS_FIELDS,
    ResourcesRepository,
)


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
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            if folder_id:
                return await self.fetch_one(
                    "SELECT * FROM resource_items "
                    "WHERE resource_id = $1 "
                    "  AND scope_id = $2 "
                    "  AND folder_id = $3 "
                    "LIMIT 1",
                    self._bigint(resource_id),
                    scope_id,
                    self._bigint(folder_id),
                )
            return await self.fetch_one(
                "SELECT * FROM resource_items "
                "WHERE resource_id = $1 "
                "  AND scope_id = $2 "
                "  AND folder_id IS NULL "
                "LIMIT 1",
                self._bigint(resource_id),
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
                f"in scope {data.get('scope_id')}"
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
        self, resource_id: str, scope_type: Optional[str], scope_id: str
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            return await self.fetch_one(
                "SELECT * FROM resource_items "
                "WHERE resource_id = $1 "
                "  AND scope_id = $2 "
                "LIMIT 1",
                self._bigint(resource_id),
                scope_id,
            )
        except Exception as e:
            logger.error(f"Failed to get resource_item: {e}")
            return None

    async def get_resource_item_in_folder(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            if folder_id:
                return await self.fetch_one(
                    "SELECT * FROM resource_items "
                    "WHERE resource_id = $1 "
                    "  AND scope_id = $2 "
                    "  AND folder_id = $3 "
                    "LIMIT 1",
                    self._bigint(resource_id),
                    scope_id,
                    self._bigint(folder_id),
                )
            return await self.fetch_one(
                "SELECT * FROM resource_items "
                "WHERE resource_id = $1 "
                "  AND scope_id = $2 "
                "  AND folder_id IS NULL "
                "LIMIT 1",
                self._bigint(resource_id),
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

    # ── Listing with filters (the 22-arg behemoth) ──────────────────
    #
    # Translation of the legacy ``get_resource_items`` from PostgREST
    # chaining to dynamic SQL. The legacy version accepts 22 params,
    # but at the repository layer only ~10 actually filter — the rest
    # (``aspect_ratios`` + the 6 social-metric chips) are no-ops kept
    # for API-surface parity and applied client-side. The asyncpg
    # version preserves the exact same no-op behaviour.

    @staticmethod
    def _build_mime_sql(types: Optional[List[str]]) -> Optional[str]:
        """Translate type categories into a SQL ``OR`` expression.
        Returns None when no filter is needed. Values are inlined as
        SQL string literals (not $N params) because they're a fixed
        set of well-known LIKE patterns — no injection surface."""
        if not types:
            return None
        categories = {t.strip() for t in types if t and t.strip()}
        if not categories:
            return None

        # The known prefixes used by both 'document' and 'other'.
        document_clause = (
            "(r.mime_type = 'application/pdf' "
            "OR r.mime_type LIKE 'application/msword%' "
            "OR r.mime_type LIKE 'application/vnd.%' "
            "OR r.mime_type LIKE 'text/%')"
        )
        known_clause = (
            "(r.mime_type LIKE 'video/%' "
            "OR r.mime_type LIKE 'image/%' "
            "OR r.mime_type LIKE 'audio/%' "
            "OR r.mime_type = 'application/pdf' "
            "OR r.mime_type LIKE 'application/msword%' "
            "OR r.mime_type LIKE 'application/vnd.%' "
            "OR r.mime_type LIKE 'text/%')"
        )

        clauses: List[str] = []
        for category in categories:
            if category == "video":
                clauses.append("r.mime_type LIKE 'video/%'")
            elif category == "image":
                clauses.append("r.mime_type LIKE 'image/%'")
            elif category == "audio":
                clauses.append("r.mime_type LIKE 'audio/%'")
            elif category == "document":
                clauses.append(document_clause)
            elif category == "other":
                clauses.append(f"NOT {known_clause}")
            # Silently ignore unknown categories — schema validation
            # is the router's job (matches legacy behaviour).

        if not clauses:
            return None
        return "(" + " OR ".join(clauses) + ")"

    async def get_resource_items(
        self,
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: Optional[str] = None,
        include_trashed: bool = False,
        tag_ids: Optional[List[str]] = None,
        min_rating: Optional[int] = None,
        types: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        ai_transcribed: Optional[bool] = None,
        ai_summarized: Optional[bool] = None,
        ai_analyzed: Optional[bool] = None,
        created_after: Optional[date] = None,
        created_before: Optional[date] = None,
        duration_min: Optional[int] = None,
        duration_max: Optional[int] = None,
        aspect_ratios: Optional[List[str]] = None,
        min_likes: Optional[int] = None,
        min_comments: Optional[int] = None,
        min_favorites: Optional[int] = None,
        min_shares: Optional[int] = None,
        social_combine: str = "and",
        has_comments: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """List resource_items joined to their resources.

        See the legacy docstring on
        ``ResourcesRepository.get_resource_items`` for full
        per-parameter semantics — this implementation preserves all
        of them, including the no-ops (``aspect_ratios`` and social
        metrics) which the frontend chip applies client-side."""
        try:
            # AND-semantic tag filter — pre-resolve via helper.
            matched_resource_ids: Optional[List[str]] = None
            if tag_ids:
                matched_resource_ids = await self._resource_ids_with_all_tags(tag_ids)
                if not matched_resource_ids:
                    return []

            # Platform pre-resolution + intersection.
            if platforms:
                platform_resource_ids = await self._resource_ids_for_platforms(
                    platforms
                )
                if not platform_resource_ids:
                    return []
                if matched_resource_ids is None:
                    matched_resource_ids = platform_resource_ids
                else:
                    platform_set = set(platform_resource_ids)
                    matched_resource_ids = [
                        rid for rid in matched_resource_ids if rid in platform_set
                    ]
                    if not matched_resource_ids:
                        return []

            # Build dynamic WHERE. Each conditional appends a clause
            # and a $N param. Order matters: same-numbered placeholders
            # must match args[] index 1:1.
            # PR-E Phase 1: scope_type no longer filters (scope_id is unique).
            where: List[str] = ["i.scope_id = $1"]
            args: List[Any] = [scope_id]

            def _ph() -> str:
                return f"${len(args) + 1}"

            if folder_id:
                where.append(f"i.folder_id = {_ph()}")
                args.append(self._bigint(folder_id))
            else:
                where.append("i.folder_id IS NULL")

            if not include_trashed:
                where.append("r.is_trashed = false")

            if matched_resource_ids is not None:
                where.append(f"i.resource_id = ANY({_ph()}::bigint[])")
                args.append(self._bigint_list(matched_resource_ids))

            if min_rating is not None:
                where.append(f"r.rating >= {_ph()}")
                args.append(int(min_rating))

            mime_sql = self._build_mime_sql(types)
            if mime_sql:
                where.append(mime_sql)

            # AI status filters: each requires == "completed".
            for flag, column in (
                (ai_transcribed, _AI_STATUS_FIELDS["transcribed"]),
                (ai_summarized, _AI_STATUS_FIELDS["summarized"]),
                (ai_analyzed, _AI_STATUS_FIELDS["analyzed"]),
            ):
                if flag is True:
                    where.append(f'r."{column}" = {_ph()}')
                    args.append(_AI_STATUS_COMPLETED)

            if created_after is not None:
                where.append(f"r.created_at >= {_ph()}")
                # asyncpg wants datetime objects, not isoformat strings.
                args.append(
                    datetime.combine(
                        created_after, datetime.min.time(), tzinfo=timezone.utc
                    )
                )
            if created_before is not None:
                where.append(f"r.created_at <= {_ph()}")
                args.append(
                    datetime.combine(
                        created_before, datetime.max.time(), tzinfo=timezone.utc
                    )
                )

            if duration_min is not None:
                where.append(f"r.duration_seconds >= {_ph()}")
                args.append(int(duration_min))
            if duration_max is not None:
                where.append(f"r.duration_seconds <= {_ph()}")
                args.append(int(duration_max))

            # Tail no-ops kept for parity (router accepts them, the
            # filter happens client-side in useResourcesDisplay).
            _ = (
                aspect_ratios,
                min_likes,
                min_comments,
                min_favorites,
                min_shares,
                social_combine,
                has_comments,
            )

            sql = (
                "SELECT i.id, i.resource_id, i.scope_id, "
                "       i.folder_id, "
                "       i.created_at AS i_created_at, "
                "       i.updated_at AS i_updated_at, "
                "       row_to_json(r.*) AS resource "
                "FROM resource_items i "
                "INNER JOIN resources r ON i.resource_id = r.id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY i.created_at DESC"
            )

            import json

            rows = await self.fetch_all(sql, *args)
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                # Restore the unprefixed column names callers expect.
                row["created_at"] = row.pop("i_created_at")
                row["updated_at"] = row.pop("i_updated_at")
            return rows
        except Exception as e:
            logger.error(f"Failed to get resource items: {e}")
            return []

    # ── Trash listing ───────────────────────────────────────────────

    async def get_expired_trashed_resources(
        self, older_than_days: int = 30
    ) -> List[Dict[str, Any]]:
        """Trashed resources older than N days, for permanent cleanup."""
        try:
            # datetime object, not isoformat — asyncpg requires it.
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            return await self.fetch_all(
                "SELECT id, file_path, cover_image_path FROM resources "
                "WHERE is_trashed = true AND trashed_at < $1",
                cutoff,
            )
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: Optional[str], scope_id: str
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
                "SELECT i.id, i.resource_id, i.scope_id, "
                "       i.folder_id, i.created_at AS i_created_at, "
                "       i.updated_at AS i_updated_at, "
                "       row_to_json(r.*) AS resource "
                "FROM resource_items i "
                "INNER JOIN resources r ON i.resource_id = r.id "
                "WHERE i.scope_id = $1 "
                "  AND r.is_trashed = true "
                "ORDER BY i.created_at DESC",
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

    # ── Resource Versions ───────────────────────────────────────────
    #
    # ``resource_versions`` is append-only history per resource. The
    # version_number column is monotonic per resource_id; reads almost
    # always sort DESC to get the latest first.

    # resource_versions BIGINT columns that snowflake ids travel into as
    # strings from FastAPI / Pydantic. asyncpg refuses to cast str → int8
    # so coerce at the boundary or every download.finalize backfill 500s.
    _VERSION_BIGINT_COLS = ("resource_id", "file_size_bytes")

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            coerced = {
                k: (self._bigint(v) if k in self._VERSION_BIGINT_COLS else v)
                for k, v in data.items()
            }
            cols = list(coerced.keys())
            placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
            col_list = ", ".join(f'"{c}"' for c in cols)
            sql = (
                f'INSERT INTO "resource_versions" ({col_list}) '
                f"VALUES ({placeholders}) RETURNING *"
            )
            row = await self.fetch_one(sql, *coerced.values())
            logger.info(
                f"Created version {coerced.get('version_number')} "
                f"for resource {coerced.get('resource_id')}"
            )
            return row or {}
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    async def get_versions(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            return await self.fetch_all(
                "SELECT * FROM resource_versions "
                "WHERE resource_id = $1 "
                "ORDER BY version_number DESC",
                self._bigint(resource_id),
            )
        except Exception as e:
            logger.error(f"Failed to get versions for resource {resource_id}: {e}")
            return []

    async def get_version_by_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resource_versions WHERE id = $1",
                self._bigint(version_id),
            )
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None

    async def get_version_by_number(
        self, resource_id: str, version_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM resource_versions "
                "WHERE resource_id = $1 AND version_number = $2 "
                "LIMIT 1",
                self._bigint(resource_id),
                int(version_number),
            )
        except Exception as e:
            logger.error(
                f"Failed to get version {version_number} for {resource_id}: {e}"
            )
            return None

    async def delete_version(self, version_id: str) -> bool:
        try:
            await self.execute(
                "DELETE FROM resource_versions WHERE id = $1",
                self._bigint(version_id),
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
            cols = list(data.keys())
            set_pairs = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(cols))
            sql = (
                f'UPDATE "resource_versions" SET {set_pairs} '
                f"WHERE id = ${len(cols) + 1} RETURNING *"
            )
            row = await self.fetch_one(sql, *data.values(), self._bigint(version_id))
            return row or {}
        except Exception as e:
            logger.error(f"Failed to update version {version_id}: {e}")
            raise

    async def get_untranscoded_video_versions(
        self,
    ) -> List[Dict[str, Any]]:
        """Video versions that have never been transcoded — NULL
        ``transcode_status`` AND non-NULL ``file_path``. The legacy
        version pulled this via PostgREST's ``.like`` + ``.is_`` +
        ``.not_.is_`` chain; SQL says it directly."""
        try:
            return await self.fetch_all(
                "SELECT id, resource_id, mime_type, file_path "
                "FROM resource_versions "
                "WHERE mime_type LIKE 'video/%' "
                "  AND transcode_status IS NULL "
                "  AND file_path IS NOT NULL"
            )
        except Exception as e:
            logger.error(f"Failed to get untranscoded video versions: {e}")
            return []

    async def get_next_version_number(self, resource_id: str) -> int:
        """Next monotonic version_number for a resource. Returns 1
        when the resource has no versions yet. Single COALESCE-MAX
        query — cheaper than the legacy SELECT...ORDER...LIMIT 1
        round-trip."""
        try:
            current = await self.fetch_value(
                "SELECT COALESCE(MAX(version_number), 0) "
                "FROM resource_versions WHERE resource_id = $1",
                self._bigint(resource_id),
            )
            return int(current or 0) + 1
        except Exception as e:
            logger.error(f"Failed to get next version for resource {resource_id}: {e}")
            return 1

    # ── Folders ─────────────────────────────────────────────────────
    #
    # Folders form a scope-rooted tree (parent_id NULL = top level
    # of a scope). Cascade operations recurse via WITH RECURSIVE
    # CTEs in PG — single round-trip vs. the legacy BFS-in-Python
    # which fired one query per level. The cascade methods preserve
    # the legacy return shape (``{trashed_folders, trashed_resources}``
    # / ``{restored_folders, restored_resources}``) so callers don't
    # need to change.

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            cols = list(data.keys())
            placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
            col_list = ", ".join(f'"{c}"' for c in cols)
            sql = (
                f'INSERT INTO "folders" ({col_list}) '
                f"VALUES ({placeholders}) RETURNING *"
            )
            row = await self.fetch_one(sql, *data.values())
            logger.info(f"Created folder: {data.get('name')}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_trashed_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            return await self.fetch_all(
                "SELECT * FROM folders "
                "WHERE scope_id = $1 "
                "  AND is_trashed = true "
                "ORDER BY trashed_at DESC",
                scope_id,
            )
        except Exception as e:
            logger.error(f"Failed to get trashed folders: {e}")
            return []

    async def get_folders(
        self,
        scope_type: Optional[str],
        scope_id: str,
        include_trashed: bool = False,
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            if include_trashed:
                return await self.fetch_all(
                    "SELECT * FROM folders "
                    "WHERE scope_id = $1 "
                    "ORDER BY sort_order ASC",
                    scope_id,
                )
            return await self.fetch_all(
                "SELECT * FROM folders "
                "WHERE scope_id = $1 "
                "  AND is_trashed = false "
                "ORDER BY sort_order ASC",
                scope_id,
            )
        except Exception as e:
            logger.error(f"Failed to get folders: {e}")
            return []

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.fetch_one(
                "SELECT * FROM folders WHERE id = $1",
                self._bigint(folder_id),
            )
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def update_folder(
        self, folder_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            cols = list(data.keys())
            set_pairs = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(cols))
            sql = (
                f'UPDATE "folders" SET {set_pairs} '
                f"WHERE id = ${len(cols) + 1} RETURNING *"
            )
            row = await self.fetch_one(sql, *data.values(), self._bigint(folder_id))
            logger.info(f"Updated folder {folder_id}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            await self.execute(
                "DELETE FROM folders WHERE id = $1",
                self._bigint(folder_id),
            )
            logger.info(f"Deleted folder {folder_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    async def get_descendant_folder_ids(self, folder_id: str) -> List[str]:
        """Recursive descent into non-trashed children. Single CTE
        replaces the legacy BFS that fired one query per tree level
        (10-level tree = 10 round-trips before; 1 now).

        Returns list[str] for legacy contract (caller passes back to
        PostgREST in some unmigrated paths); internal callers like
        ``count_folder_contents`` re-coerce via ``_bigint_list``."""
        try:
            rows = await self.fetch_all(
                "WITH RECURSIVE descendants AS ("
                "  SELECT id FROM folders "
                "    WHERE parent_id = $1 AND is_trashed = false "
                "  UNION ALL "
                "  SELECT f.id FROM folders f "
                "    INNER JOIN descendants d ON f.parent_id = d.id "
                "    WHERE f.is_trashed = false"
                ") SELECT id FROM descendants",
                self._bigint(folder_id),
            )
            return [str(r["id"]) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get descendant folders for {folder_id}: {e}")
            return []

    async def count_folder_contents(self, folder_ids: List[str]) -> Dict[str, int]:
        """Resource + sub-folder count for a list of folder ids.

        Sub-folder count mirrors legacy quirk: ``len(ids) - 1`` (the
        list is expected to be ``[root] + descendants`` from
        get_descendant_folder_ids; the -1 strips the root itself).

        ``folder_id`` column is bigint; the ``::bigint[]`` cast +
        per-element coercion is required because asyncpg won't bind
        a list of str to a bigint array."""
        try:
            resource_count = await self.fetch_value(
                "SELECT count(*) FROM resource_items "
                "WHERE folder_id = ANY($1::bigint[])",
                self._bigint_list(folder_ids),
            )
            subfolder_count = len(folder_ids) - 1 if len(folder_ids) > 1 else 0
            return {
                "resource_count": int(resource_count or 0),
                "subfolder_count": subfolder_count,
            }
        except Exception as e:
            logger.error(f"Failed to count folder contents: {e}")
            return {"resource_count": 0, "subfolder_count": 0}

    async def restore_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Restore a folder + all trashed descendants + their
        resources, in one transaction. Single CTE per phase replaces
        the legacy per-folder loop.

        ``all_ids`` is kept as int (not stringified) for the bulk
        UPDATEs — folder_id and resources.id are both bigint, and
        ``::bigint[]`` is the only safe binding."""
        try:
            async with self.transaction() as conn:
                folder_ids_rows = await self._conn_fetch_all(
                    conn,
                    "WITH RECURSIVE subtree AS ("
                    "  SELECT id FROM folders "
                    "    WHERE id = $1 AND is_trashed = true "
                    "  UNION ALL "
                    "  SELECT f.id FROM folders f "
                    "    INNER JOIN subtree s ON f.parent_id = s.id "
                    "    WHERE f.is_trashed = true"
                    ") SELECT id FROM subtree",
                    self._bigint(folder_id),
                )
                all_ids = [r["id"] for r in folder_ids_rows]
                if not all_ids:
                    return {"restored_folders": 0, "restored_resources": 0}

                restored_resources_rows = await self._conn_fetch_all(
                    conn,
                    "UPDATE resources SET is_trashed = false, "
                    "       trashed_at = NULL "
                    "WHERE is_trashed = true "
                    "  AND id IN ("
                    "    SELECT resource_id FROM resource_items "
                    "    WHERE folder_id = ANY($1::bigint[])"
                    "  ) RETURNING id",
                    all_ids,
                )
                restored_folders_rows = await self._conn_fetch_all(
                    conn,
                    "UPDATE folders SET is_trashed = false, "
                    "       trashed_at = NULL "
                    "WHERE id = ANY($1::bigint[]) RETURNING id",
                    all_ids,
                )

            restored_folders = len(restored_folders_rows)
            restored_resources = len(restored_resources_rows)
            logger.info(
                f"Cascade-restored folder {folder_id}: "
                f"{restored_folders} folders, "
                f"{restored_resources} resources"
            )
            return {
                "restored_folders": restored_folders,
                "restored_resources": restored_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-restore folder {folder_id}: {e}")
            raise

    async def trash_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Trash a folder + all non-trashed descendants + their
        resources, in one transaction. The resource UPDATE preserves
        the original location (last_folder_id / last_library_id /
        last_scope_*) per legacy contract — needed for restore."""
        try:
            async with self.transaction() as conn:
                # Subtree of non-trashed folders rooted at folder_id.
                # Includes the root regardless of its trashed state
                # (legacy behaviour: trash_folder_cascade always
                # processes the root).
                folder_ids_rows = await self._conn_fetch_all(
                    conn,
                    "WITH RECURSIVE subtree AS ("
                    "  SELECT id FROM folders WHERE id = $1 "
                    "  UNION ALL "
                    "  SELECT f.id FROM folders f "
                    "    INNER JOIN subtree s ON f.parent_id = s.id "
                    "    WHERE f.is_trashed = false"
                    ") SELECT id FROM subtree",
                    self._bigint(folder_id),
                )
                all_ids = [r["id"] for r in folder_ids_rows]
                if not all_ids:
                    return {"trashed_folders": 0, "trashed_resources": 0}

                # Trash resources currently in any of these folders.
                # Snapshot last_* fields from resource_items so a
                # later restore can put them back where they were.
                trashed_resources_rows = await self._conn_fetch_all(
                    conn,
                    "UPDATE resources r SET "
                    "  is_trashed = true, "
                    "  trashed_at = now(), "
                    "  last_folder_id = i.folder_id, "
                    "  last_library_id = i.library_id, "
                    "  last_scope_type = i.scope_type, "
                    "  last_scope_id = i.scope_id "
                    "FROM resource_items i "
                    "WHERE r.id = i.resource_id "
                    "  AND i.folder_id = ANY($1::bigint[]) "
                    "  AND r.is_trashed = false RETURNING r.id",
                    all_ids,
                )
                trashed_folders_rows = await self._conn_fetch_all(
                    conn,
                    "UPDATE folders SET "
                    "  is_trashed = true, trashed_at = now() "
                    "WHERE id = ANY($1::bigint[]) RETURNING id",
                    all_ids,
                )

            trashed_folders = len(trashed_folders_rows)
            trashed_resources = len(trashed_resources_rows)
            logger.info(
                f"Cascade-trashed folder {folder_id}: "
                f"{trashed_folders} folders, "
                f"{trashed_resources} resources"
            )
            return {
                "trashed_folders": trashed_folders,
                "trashed_resources": trashed_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-trash folder {folder_id}: {e}")
            raise


__all__ = ["ResourcesRepositoryAsyncpg"]
