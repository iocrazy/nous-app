"""SQLAlchemy 2.0 ORM implementation of ResourcesRepository (Task 5.2).

The successor to ``resources_repository_asyncpg.py``. Same Strangler-Fig
multiple-inheritance pattern (overrides the 40 data-access methods that
touch ``resources`` / ``resource_items`` / ``resource_versions`` /
``folders``; inherits the remaining legacy-only methods — the
resource_tags trio + smart-folder trio — from the supabase-py base via
Python MRO), but the internals run on the ORM session scopes from
``app.db.session`` instead of ``db_engine.fetch_one``/``execute`` on a
bare ``connect()``.

THE P0 FIX
==========
The asyncpg path ran every write via ``self.fetch_one("…RETURNING")`` →
``db_engine.fetch_one()`` on a NON-committing ``engine.connect()``. On
connection close the INSERT/UPDATE/DELETE **silently rolled back** — the
returned RETURNING row looked written but the next read saw the OLD value
(silent data loss). This implementation routes every write through
``write_scope()`` (which does ``session.begin()`` and COMMITS), and runs
each multi-statement cascade inside ONE ``write_scope()`` so the cascade
is atomic (all-or-nothing) AND actually persists. The P0-persistence and
cascade-atomicity regression tests in
``tests/integration/test_resources_repository_orm.py`` pin this.

Fidelity contract (the swap must be invisible to all call sites):
  - dict at the boundary — never leak ORM objects. Same exact dict shapes
    as the asyncpg/legacy impls (column projections, nested ``resource``
    from ``row_to_json``, ``parsed_media`` overlay, cascade count dicts).
  - ``_bigint()`` / ``_bigint_list()`` coercion on str-snowflake ids
    before binding to BIGINT columns (asyncpg int8 codec is strict).
  - ``_VERSION_BIGINT_COLS`` coercion preserved in ``create_version``.
  - datetimes bound as tz-aware ``datetime`` objects, never isoformat.
  - enum read-parity: ``resources`` has 3 ``Enum(AiTaskStatus)`` columns
    (transcript_status / summary_status / visual_analysis_status). The ORM
    returns enum MEMBERS; the prior impls returned bare ``str``. Every read
    of a resources row is funnelled through ``_resources_row_to_dict`` which
    unwraps enums to ``.value`` (see ``_plain``).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, text, update

from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.models import Folders, ResourceItems, Resources, ResourceVersions
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict, _plain
from app.repositories.resources_repository import (
    _AI_STATUS_COMPLETED,
    _AI_STATUS_FIELDS,
    ResourcesRepository,
)

_RESOURCES_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Resources)
_RESOURCE_ITEMS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceItems)
_RESOURCE_VERSIONS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceVersions)
_FOLDERS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Folders)


def _resources_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resources`` ORM row (enum-safe)."""
    return _orm_obj_to_dict(obj, _RESOURCES_NAME_TO_ATTR)


def _mappings_dict(row: Any) -> Dict[str, Any]:
    """Plain dict from a RETURNING ``.mappings()`` row, enum-unwrapped.

    RETURNING rows still pass through the column type result processors, so
    an ``Enum`` column comes back as an enum MEMBER here too — funnel through
    ``_plain`` for the same bare-str parity as reads."""
    return {k: _plain(v) for k, v in dict(row).items()}


class ResourcesRepositoryOrm(AsyncpgRepository, ResourcesRepository):
    """ORM-backed ResourcesRepository.

    Overrides the 40 data-access methods on the four tables; the
    resource_tags trio (add/remove/get) and smart-folder trio
    (get/create/execute) inherit the legacy supabase-py path via MRO.
    ``_bigint`` / ``_bigint_list`` are inherited from AsyncpgRepository."""

    TABLE = "resources"

    # ── Resources CRUD ──────────────────────────────────────────────

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Resources)
                    .values(**data)
                    .returning(*Resources.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(f"Created resource: {data.get('filename')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create resource: {e}")
            raise

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.id == self._bigint(resource_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_media_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource by media_id {media_id}: {e}")
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Two-step lookup parsed_media.platform_id → resources.media_id.

        Kept as two queries (rather than a JOIN) to match legacy shape —
        caller may already have the parsed_media row cached and collapsing
        into a JOIN would diverge the cache key."""
        try:
            async with read_scope() as session:
                # parsed_media.platform_id → id (text lookup). Use raw text()
                # so we don't import ParsedMedia here just for one scalar.
                media_id = await session.scalar(
                    text(
                        "SELECT id FROM parsed_media "
                        "WHERE platform_id = :pid LIMIT 1"
                    ),
                    {"pid": platform_id},
                )
            if not media_id:
                return None
            # parsed_media.id and resources.media_id are both BIGINT
            # (Snowflake, migration 051); pass the int through directly.
            return await self.get_resource_by_media_id(media_id)
        except Exception as e:
            logger.error(f"Failed to get resource by platform_id {platform_id}: {e}")
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                    .where(Resources.creator_id == creator_id)
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get resource for media={media_id}, "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_completed_resource_by_url_and_creator(
        self, url: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """L2 dedup probe. JOIN with parsed_media on media_id, filter on
        original_url and the appropriate completion status (image vs video).

        Returns the same nested shape as legacy: ``{id, media_id,
        parsed_media: {id, platform_id, original_url, ...}}`` so call sites
        read the inner dict the same way."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT r.id AS r_id, r.media_id AS r_media_id, "
                        "       p.id AS p_id, p.platform_id, p.original_url, "
                        "       p.video_download_status, p.image_download_status, "
                        "       p.media_type "
                        "FROM resources r "
                        "INNER JOIN parsed_media p ON r.media_id = p.id "
                        "WHERE r.creator_id = :creator_id "
                        "  AND p.original_url = :url "
                        "LIMIT 1"
                    ),
                    {"creator_id": creator_id, "url": url},
                )
                row = result.mappings().first()
            if not row:
                return None
            row = dict(row)

            mt = row.get("media_type")
            is_image = str(mt) in ("2", "68", "image", "images")
            status = (
                row.get("image_download_status")
                if is_image
                else row.get("video_download_status")
            )
            # Statuses come back as the PG enum's text value (str) via text();
            # funnel through _plain in case the driver hands back a member.
            if _plain(status) != "completed":
                return None

            return {
                "id": row["r_id"],
                "media_id": row["r_media_id"],
                "parsed_media": {
                    "id": row["p_id"],
                    "platform_id": row["platform_id"],
                    "original_url": row["original_url"],
                    "video_download_status": _plain(row["video_download_status"]),
                    "image_download_status": _plain(row["image_download_status"]),
                    "media_type": row["media_type"],
                },
            }
        except Exception as e:
            logger.debug(
                f"[ResourcesRepo] L2 dedup probe failed url={url[:40]} "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_owned_platform_ids(
        self, platform_ids: List[str], creator_id: str
    ) -> set:
        """Of the given vids (parsed_media.platform_id), return the subset this
        user has already downloaded (a resources row with file_path set). ONE
        batched query for the whole list — no N+1. Empty input short-circuits
        to ``set()`` without a query."""
        if not platform_ids:
            return set()
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT DISTINCT p.platform_id "
                        "FROM resources r "
                        "INNER JOIN parsed_media p ON r.media_id = p.id "
                        "WHERE r.creator_id = :creator_id "
                        "  AND p.platform_id = ANY(:pids) "
                        "  AND r.file_path IS NOT NULL"
                    ),
                    {"creator_id": creator_id, "pids": list(platform_ids)},
                )
                return {r["platform_id"] for r in result.mappings().all()}
        except Exception as e:
            logger.error(
                f"Failed to resolve owned platform_ids for creator "
                f"{creator_id}: {e}"
            )
            return set()

    async def update_resource(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """UPDATE a resource by id, COMMITTING via write_scope (the P0 fix)."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(Resources)
                    .where(Resources.id == self._bigint(resource_id))
                    .values(**data)
                    .returning(*Resources.__table__.columns)
                )
                row = result.mappings().first()
                updated = _mappings_dict(row) if row else {}
            logger.info(f"Updated resource {resource_id}")
            return updated
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(Resources).where(
                        Resources.id == self._bigint(resource_id)
                    )
                )
            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            raise

    async def count_resources_by_media_id(self, media_id: str) -> int:
        try:
            async with read_scope() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                )
            return int(count or 0)
        except Exception as e:
            logger.error(f"Failed to count resources for media {media_id}: {e}")
            return 0

    # ── Hash-based duplicate lookup ─────────────────────────────────

    async def find_by_hash(self, file_hash: str, creator_id: str) -> list[dict]:
        """Find non-trashed resources with the same file hash for a given
        creator. Column projection matches legacy exactly."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.filename,
                        Resources.file_type,
                        Resources.mime_type,
                        Resources.file_size_bytes,
                        Resources.thumbnail_path,
                        Resources.cover_image_path,
                        Resources.created_at,
                    )
                    .where(Resources.file_hash == file_hash)
                    .where(Resources.creator_id == creator_id)
                    .where(Resources.is_trashed.is_(False))
                )
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []

    # ── Resource Items ──────────────────────────────────────────────

    async def find_resource_item(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = (
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                )
                if folder_id:
                    stmt = stmt.where(
                        ResourceItems.folder_id == self._bigint(folder_id)
                    )
                else:
                    stmt = stmt.where(ResourceItems.folder_id.is_(None))
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                return (
                    _orm_obj_to_dict(row, _RESOURCE_ITEMS_NAME_TO_ATTR) if row else None
                )
        except Exception as e:
            logger.error(f"Failed to find resource_item: {e}")
            return None

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ResourceItems)
                    .values(**data)
                    .returning(*ResourceItems.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(
                f"Created resource_item for resource {data.get('resource_id')} "
                f"in scope {data.get('scope_id')}"
            )
            return created
        except Exception as e:
            logger.error(f"Failed to create resource_item: {e}")
            raise

    async def _resource_ids_for_platforms(self, platforms: List[str]) -> List[str]:
        """Resource ids whose linked parsed_media.source_platform is in
        ``platforms``. Two-step lookup retained for parity. Returns list[str]
        (the inherited get_resource_items legacy path feeds it to PostgREST)."""
        cleaned = [p.strip() for p in platforms if p and p.strip()]
        if not cleaned:
            return []
        try:
            async with read_scope() as session:
                media_rows = await session.execute(
                    text(
                        "SELECT id FROM parsed_media "
                        "WHERE source_platform = ANY(:platforms)"
                    ),
                    {"platforms": cleaned},
                )
                media_ids_int = [r["id"] for r in media_rows.mappings().all()]
                if not media_ids_int:
                    return []
                resource_rows = await session.execute(
                    text(
                        "SELECT id FROM resources " "WHERE media_id = ANY(:media_ids)"
                    ),
                    {"media_ids": media_ids_int},
                )
                return [str(r["id"]) for r in resource_rows.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to resolve resource ids for platforms: {e}")
            return []

    async def _resource_ids_with_all_tags(self, tag_ids: List[str]) -> List[str]:
        """Resource ids that carry EVERY tag in ``tag_ids`` (AND).

        Single-query GROUP BY HAVING count = N. ``tag_id`` is a bigint column
        (migration 077+); coerce the str ids and bind as a bigint array."""
        if not tag_ids:
            return []
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT resource_id FROM resource_tags "
                        "WHERE tag_id = ANY(:tag_ids) "
                        "GROUP BY resource_id "
                        "HAVING count(DISTINCT tag_id) = :n"
                    ),
                    {
                        "tag_ids": self._bigint_list(tag_ids),
                        "n": len(set(tag_ids)),
                    },
                )
                return [str(r["resource_id"]) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to intersect resource tag ids: {e}")
            return []

    async def get_resource_item(
        self, resource_id: str, scope_type: Optional[str], scope_id: str
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return (
                    _orm_obj_to_dict(row, _RESOURCE_ITEMS_NAME_TO_ATTR) if row else None
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
            async with read_scope() as session:
                stmt = (
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                )
                if folder_id:
                    stmt = stmt.where(
                        ResourceItems.folder_id == self._bigint(folder_id)
                    )
                else:
                    stmt = stmt.where(ResourceItems.folder_id.is_(None))
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                return (
                    _orm_obj_to_dict(row, _RESOURCE_ITEMS_NAME_TO_ATTR) if row else None
                )
        except Exception as e:
            logger.error(f"Failed to get resource_item in folder: {e}")
            return None

    async def get_first_resource_item(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return (
                    _orm_obj_to_dict(row, _RESOURCE_ITEMS_NAME_TO_ATTR) if row else None
                )
        except Exception as e:
            logger.error(f"Failed to get first resource_item: {e}")
            return None

    async def update_resource_item(
        self, item_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ResourceItems)
                    .where(ResourceItems.id == self._bigint(item_id))
                    .values(**data)
                    .returning(*ResourceItems.__table__.columns)
                )
                row = result.mappings().first()
                return _mappings_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update resource_item {item_id}: {e}")
            raise

    async def delete_resource_item(self, item_id: str) -> bool:
        """Delete a resource_item by ID. The DB trigger auto-trashes the
        parent resource if this was the last reference."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ResourceItems).where(
                        ResourceItems.id == self._bigint(item_id)
                    )
                )
            logger.info(f"Deleted resource_item {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource_item {item_id}: {e}")
            raise

    async def count_resource_items(self, resource_id: str) -> int:
        try:
            async with read_scope() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                )
            return int(count or 0)
        except Exception as e:
            logger.error(f"Failed to count items for resource {resource_id}: {e}")
            return 0

    # ── Listing with filters (the 22-arg behemoth) ──────────────────

    @staticmethod
    def _build_mime_sql(types: Optional[List[str]]) -> Optional[str]:
        """Translate type categories into a SQL ``OR`` expression. Returns
        None when no filter is needed. Values are inlined as SQL string
        literals (a fixed set of well-known LIKE patterns — no injection
        surface)."""
        if not types:
            return None
        categories = {t.strip() for t in types if t and t.strip()}
        if not categories:
            return None

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
            # Silently ignore unknown categories (matches legacy behaviour).

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

        Same dynamic-WHERE SQL the asyncpg impl built; the embedded
        ``resource`` is produced by ``row_to_json(r.*)`` and decoded to a
        dict. Preserves all per-parameter semantics including the no-ops
        (``aspect_ratios`` + social metrics) the frontend chip applies
        client-side. Bound named params (:name) — no asyncpg ``$N``."""
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

            # Build dynamic WHERE with named bind params.
            where: List[str] = ["i.scope_id = :scope_id"]
            params: Dict[str, Any] = {"scope_id": self._bigint(scope_id)}

            if folder_id:
                where.append("i.folder_id = :folder_id")
                params["folder_id"] = self._bigint(folder_id)
            else:
                where.append("i.folder_id IS NULL")

            if not include_trashed:
                where.append("r.is_trashed = false")

            if matched_resource_ids is not None:
                where.append("i.resource_id = ANY(:matched_ids)")
                params["matched_ids"] = self._bigint_list(matched_resource_ids)

            if min_rating is not None:
                where.append("r.rating >= :min_rating")
                params["min_rating"] = int(min_rating)

            mime_sql = self._build_mime_sql(types)
            if mime_sql:
                where.append(mime_sql)

            # AI status filters: each requires == "completed".
            for key, flag, column in (
                ("ai_transcribed", ai_transcribed, _AI_STATUS_FIELDS["transcribed"]),
                ("ai_summarized", ai_summarized, _AI_STATUS_FIELDS["summarized"]),
                ("ai_analyzed", ai_analyzed, _AI_STATUS_FIELDS["analyzed"]),
            ):
                if flag is True:
                    where.append(f'r."{column}" = :{key}')
                    params[key] = _AI_STATUS_COMPLETED

            if created_after is not None:
                where.append("r.created_at >= :created_after")
                params["created_after"] = datetime.combine(
                    created_after, datetime.min.time(), tzinfo=timezone.utc
                )
            if created_before is not None:
                where.append("r.created_at <= :created_before")
                params["created_before"] = datetime.combine(
                    created_before, datetime.max.time(), tzinfo=timezone.utc
                )

            if duration_min is not None:
                where.append("r.duration_seconds >= :duration_min")
                params["duration_min"] = int(duration_min)
            if duration_max is not None:
                where.append("r.duration_seconds <= :duration_max")
                params["duration_max"] = int(duration_max)

            # Tail no-ops kept for parity (filtered client-side).
            _ = (
                aspect_ratios,
                min_likes,
                min_comments,
                min_favorites,
                min_shares,
                social_combine,
                has_comments,
            )

            # NOTE: resource_items has NO ``updated_at`` column in the current
            # schema (PR-E era). The legacy ``select("*, resource:...")``
            # returned only the columns that exist, so callers never depend on
            # an item-level ``updated_at``. We select the real item columns +
            # the embedded ``resource`` (row_to_json, decoded to a dict) and
            # alias i.created_at so it survives the resource overlay.
            sql = (
                "SELECT i.id, i.resource_id, i.scope_id, "
                "       i.folder_id, "
                "       i.created_at AS i_created_at, "
                "       row_to_json(r.*) AS resource "
                "FROM resource_items i "
                "INNER JOIN resources r ON i.resource_id = r.id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY i.created_at DESC"
            )

            async with read_scope() as session:
                result = await session.execute(text(sql), params)
                rows = [dict(r) for r in result.mappings().all()]
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                row["created_at"] = row.pop("i_created_at")
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
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.file_path,
                        Resources.cover_image_path,
                    )
                    .where(Resources.is_trashed.is_(True))
                    .where(Resources.trashed_at < cutoff)
                )
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        """Trashed resources in a scope. JOIN with resources, re-shape flat
        row → ``{item_columns..., resource: {...}}`` to match the
        embedded-PostgREST shape callers depend on. ``row_to_json(r.*)`` is
        decoded explicitly (the driver returns it as a str)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT i.id, i.resource_id, i.scope_id, "
                        "       i.folder_id, i.created_at AS i_created_at, "
                        "       row_to_json(r.*) AS resource "
                        "FROM resource_items i "
                        "INNER JOIN resources r ON i.resource_id = r.id "
                        "WHERE i.scope_id = :scope_id "
                        "  AND r.is_trashed = true "
                        "ORDER BY i.created_at DESC"
                    ),
                    {"scope_id": self._bigint(scope_id)},
                )
                rows = [dict(r) for r in result.mappings().all()]
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                row["created_at"] = row.pop("i_created_at")
            return rows
        except Exception as e:
            logger.error(f"Failed to get trashed resources: {e}")
            return []

    # ── Resource Versions ───────────────────────────────────────────

    # resource_versions BIGINT columns that snowflake ids travel into as
    # strings. asyncpg refuses to cast str → int8 so coerce at the boundary.
    _VERSION_BIGINT_COLS = ("resource_id", "file_size_bytes")

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            coerced = {
                k: (self._bigint(v) if k in self._VERSION_BIGINT_COLS else v)
                for k, v in data.items()
            }
            async with write_scope() as session:
                result = await session.execute(
                    insert(ResourceVersions)
                    .values(**coerced)
                    .returning(*ResourceVersions.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(
                f"Created version {coerced.get('version_number')} "
                f"for resource {coerced.get('resource_id')}"
            )
            return created
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    async def get_versions(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.resource_id == self._bigint(resource_id))
                    .order_by(ResourceVersions.version_number.desc())
                )
                return [
                    _orm_obj_to_dict(r, _RESOURCE_VERSIONS_NAME_TO_ATTR)
                    for r in result.scalars().all()
                ]
        except Exception as e:
            logger.error(f"Failed to get versions for resource {resource_id}: {e}")
            return []

    async def get_version_by_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.id == self._bigint(version_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return (
                    _orm_obj_to_dict(row, _RESOURCE_VERSIONS_NAME_TO_ATTR)
                    if row
                    else None
                )
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None

    async def get_version_by_number(
        self, resource_id: str, version_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.resource_id == self._bigint(resource_id))
                    .where(ResourceVersions.version_number == int(version_number))
                    .limit(1)
                )
                row = result.scalars().first()
                return (
                    _orm_obj_to_dict(row, _RESOURCE_VERSIONS_NAME_TO_ATTR)
                    if row
                    else None
                )
        except Exception as e:
            logger.error(
                f"Failed to get version {version_number} for {resource_id}: {e}"
            )
            return None

    async def delete_version(self, version_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ResourceVersions).where(
                        ResourceVersions.id == self._bigint(version_id)
                    )
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
            async with write_scope() as session:
                result = await session.execute(
                    update(ResourceVersions)
                    .where(ResourceVersions.id == self._bigint(version_id))
                    .values(**data)
                    .returning(*ResourceVersions.__table__.columns)
                )
                row = result.mappings().first()
                return _mappings_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update version {version_id}: {e}")
            raise

    async def get_untranscoded_video_versions(self) -> List[Dict[str, Any]]:
        """Video versions never transcoded — NULL ``transcode_status`` AND
        non-NULL ``file_path``. Projection matches legacy."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        ResourceVersions.id,
                        ResourceVersions.resource_id,
                        ResourceVersions.mime_type,
                        ResourceVersions.file_path,
                    )
                    .where(ResourceVersions.mime_type.like("video/%"))
                    .where(ResourceVersions.transcode_status.is_(None))
                    .where(ResourceVersions.file_path.isnot(None))
                )
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get untranscoded video versions: {e}")
            return []

    async def get_next_version_number(self, resource_id: str) -> int:
        """Next monotonic version_number for a resource. Returns 1 when the
        resource has no versions yet."""
        try:
            async with read_scope() as session:
                current = await session.scalar(
                    select(
                        func.coalesce(func.max(ResourceVersions.version_number), 0)
                    ).where(ResourceVersions.resource_id == self._bigint(resource_id))
                )
            return int(current or 0) + 1
        except Exception as e:
            logger.error(f"Failed to get next version for resource {resource_id}: {e}")
            return 1

    # ── Folders ─────────────────────────────────────────────────────

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Folders).values(**data).returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(f"Created folder: {data.get('name')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_trashed_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.scope_id == self._bigint(scope_id))
                    .where(Folders.is_trashed.is_(True))
                    .order_by(Folders.trashed_at.desc())
                )
                return [
                    _orm_obj_to_dict(r, _FOLDERS_NAME_TO_ATTR)
                    for r in result.scalars().all()
                ]
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
            async with read_scope() as session:
                stmt = select(Folders).where(Folders.scope_id == self._bigint(scope_id))
                if not include_trashed:
                    stmt = stmt.where(Folders.is_trashed.is_(False))
                stmt = stmt.order_by(Folders.sort_order.asc())
                result = await session.execute(stmt)
                return [
                    _orm_obj_to_dict(r, _FOLDERS_NAME_TO_ATTR)
                    for r in result.scalars().all()
                ]
        except Exception as e:
            logger.error(f"Failed to get folders: {e}")
            return []

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.id == self._bigint(folder_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _orm_obj_to_dict(row, _FOLDERS_NAME_TO_ATTR) if row else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def update_folder(
        self, folder_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(Folders)
                    .where(Folders.id == self._bigint(folder_id))
                    .values(**data)
                    .returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                updated = _mappings_dict(row) if row else {}
            logger.info(f"Updated folder {folder_id}")
            return updated
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(Folders).where(Folders.id == self._bigint(folder_id))
                )
            logger.info(f"Deleted folder {folder_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    async def get_descendant_folder_ids(self, folder_id: str) -> List[str]:
        """Recursive descent into non-trashed children. Single CTE.

        Returns list[str] for legacy contract (some unmigrated paths pass it
        back to PostgREST); internal callers like ``count_folder_contents``
        re-coerce via ``_bigint_list``."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "WITH RECURSIVE descendants AS ("
                        "  SELECT id FROM folders "
                        "    WHERE parent_id = :fid AND is_trashed = false "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN descendants d ON f.parent_id = d.id "
                        "    WHERE f.is_trashed = false"
                        ") SELECT id FROM descendants"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                return [str(r["id"]) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get descendant folders for {folder_id}: {e}")
            return []

    async def count_folder_contents(self, folder_ids: List[str]) -> Dict[str, int]:
        """Resource + sub-folder count for a list of folder ids. Sub-folder
        count mirrors the legacy quirk: ``len(ids) - 1`` (the list is
        ``[root] + descendants``; the -1 strips the root)."""
        try:
            async with read_scope() as session:
                resource_count = await session.scalar(
                    text(
                        "SELECT count(*) FROM resource_items "
                        "WHERE folder_id = ANY(:fids)"
                    ),
                    {"fids": self._bigint_list(folder_ids)},
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
        """Restore a folder + all trashed descendants + their resources, in
        ONE transaction (write_scope commits once). The multi-statement
        cascade is atomic — any raise rolls back all of it."""
        try:
            async with write_scope() as session:
                folder_ids_rows = await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        "  SELECT id FROM folders "
                        "    WHERE id = :fid AND is_trashed = true "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN subtree s ON f.parent_id = s.id "
                        "    WHERE f.is_trashed = true"
                        ") SELECT id FROM subtree"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                all_ids = [r["id"] for r in folder_ids_rows.mappings().all()]
                if not all_ids:
                    return {"restored_folders": 0, "restored_resources": 0}

                restored_resources_rows = await session.execute(
                    text(
                        "UPDATE resources SET is_trashed = false, "
                        "       trashed_at = NULL "
                        "WHERE is_trashed = true "
                        "  AND id IN ("
                        "    SELECT resource_id FROM resource_items "
                        "    WHERE folder_id = ANY(:ids)"
                        "  ) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                restored_resources = len(restored_resources_rows.mappings().all())

                restored_folders_rows = await session.execute(
                    text(
                        "UPDATE folders SET is_trashed = false, "
                        "       trashed_at = NULL "
                        "WHERE id = ANY(:ids) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                restored_folders = len(restored_folders_rows.mappings().all())

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
        """Trash a folder + all non-trashed descendants + their resources, in
        ONE transaction (write_scope commits once; atomic). The resource
        UPDATE snapshots the original location (last_folder_id / last_library_id
        / last_scope_id) per legacy contract — needed for restore.

        PR-E 4c dropped ``resource_items.scope_type``; the legacy supabase-py
        impl no longer reads/snapshots it (restore uses last_scope_id + folder/
        library). We match that — the retired asyncpg impl still referenced
        ``i.scope_type`` and would 42703 against the current schema."""
        try:
            async with write_scope() as session:
                folder_ids_rows = await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        "  SELECT id FROM folders WHERE id = :fid "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN subtree s ON f.parent_id = s.id "
                        "    WHERE f.is_trashed = false"
                        ") SELECT id FROM subtree"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                all_ids = [r["id"] for r in folder_ids_rows.mappings().all()]
                if not all_ids:
                    return {"trashed_folders": 0, "trashed_resources": 0}

                trashed_resources_rows = await session.execute(
                    text(
                        "UPDATE resources r SET "
                        "  is_trashed = true, "
                        "  trashed_at = now(), "
                        "  last_folder_id = i.folder_id, "
                        "  last_library_id = i.library_id, "
                        "  last_scope_id = i.scope_id "
                        "FROM resource_items i "
                        "WHERE r.id = i.resource_id "
                        "  AND i.folder_id = ANY(:ids) "
                        "  AND r.is_trashed = false RETURNING r.id"
                    ),
                    {"ids": all_ids},
                )
                trashed_resources = len(trashed_resources_rows.mappings().all())

                trashed_folders_rows = await session.execute(
                    text(
                        "UPDATE folders SET "
                        "  is_trashed = true, trashed_at = now() "
                        "WHERE id = ANY(:ids) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                trashed_folders = len(trashed_folders_rows.mappings().all())

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


__all__ = ["ResourcesRepositoryOrm"]
