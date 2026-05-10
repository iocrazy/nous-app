"""asyncpg + Supavisor implementation of MediaRepository.

Phase 4 of the supabase-py → asyncpg migration. Lands in slices so
each PR stays reviewable:

  - Phase 4a: ``parsed_media`` table CRUD (6 methods).
    + 9 methods that wrap these (check_*, get_music_data, mark_*,
    mark_download_failed) automatically benefit via MRO since they
    call ``self.get_by_platform_id`` / ``self.update`` internally.
  - Phase 4b: bulk + list methods (mark_stale_downloads_failed,
    get_pending_downloads, get_all, get_user_media_list).
  - Phase 4c: search + statistics.

Strategy: same multiple-inheritance Strangler Fig pattern as
``ResourcesRepositoryAsyncpg``. The factory in ``media_repository.py``
returns this subclass when ``USE_ASYNCPG_MEDIA`` is on; unmigrated
methods inherit the legacy supabase-py path via MRO.

Type notes:
  - ``parsed_media.id`` is BIGINT (Snowflake — verified empirically
    against information_schema). asyncpg's int8 codec is strict and
    rejects str input → ``self._bigint`` coerces at every call site.
  - ``parsed_media.platform_id`` is TEXT — str works directly.
  - ``resources.creator_id`` is UUID — asyncpg's uuid codec accepts
    str, no coercion needed.
  - ``resources.media_id`` is BIGINT — same coercion rule as
    parsed_media.id when binding from Python.

Behavioural parity vs legacy:
  - Same return shapes (dict | None, dict, bool, list[dict])
  - Same enum / datetime preprocessing on create + update
  - Same elaborate APIError-style logging on update (now adapted to
    asyncpg.exceptions; both surface useful info on .args[0])
  - INFO log on every successful create / update / delete
  - get_user_media_list / search return dicts with ``resource_id``
    overlaid — embedded-PostgREST shape preserved
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.enums import DownloadStatus
from app.db.repository_base import AsyncpgRepository
from app.repositories.media_repository import MediaRepository

# Whitelist of column names safe to splice into ORDER BY clauses.
# Anything outside this set falls back to ``created_at`` to neutralize
# SQL injection via the ``order_by`` kwarg. Mirrors the columns the
# Library / Search / Feed UIs actually sort by.
_SAFE_PARSED_MEDIA_ORDER_COLS = {
    "created_at",
    "updated_at",
    "published_at",
    "last_viewed_at",
    "title",
    "author",
    "duration",
    "view_count",
    "like_count",
    "comment_count",
    "favorite_count",
    "share_count",
}

_SAFE_RESOURCES_ORDER_COLS = {
    "created_at",
    "updated_at",
}

# parsed_media columns that hold a download status enum. Field name is
# spliced into UPDATE SQL by mark_stale_downloads_failed — keep this set
# CLOSED so the splice can never come from caller input.
_DOWNLOAD_STATUS_FIELDS = (
    "video_download_status",
    "music_download_status",
    "cover_download_status",
    "image_download_status",
)


def _normalize_for_pg(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce DownloadStatus enum values for the parsed_media table.
    Returns a NEW dict — caller's input is not mutated (mirrors
    immutability rule).

    Datetimes are passed through AS-IS — asyncpg's timestamp codec
    expects ``datetime.datetime`` instances, NOT isoformat strings.
    The legacy supabase-py code had to ``.isoformat()`` because
    PostgREST consumed JSON. asyncpg is the opposite: ``str`` raises
    ``DataError: expected a datetime.datetime instance, got 'str'``.
    Caught by tests/integration/test_asyncpg_repos.py."""
    out = dict(data)
    for field in _DOWNLOAD_STATUS_FIELDS:
        v = out.get(field)
        if isinstance(v, DownloadStatus):
            out[field] = v.value
    return out


def _safe_order(order_by: str, allowed: set, default: str = "created_at") -> str:
    """Validate ``order_by`` against an allow-list. Anything not in the
    set falls back to ``default`` — prevents the column name from
    becoming an injection vector when spliced into ORDER BY."""
    return order_by if order_by in allowed else default


class MediaRepositoryAsyncpg(AsyncpgRepository, MediaRepository):
    """asyncpg-backed MediaRepository for the ``parsed_media`` table.

    Overrides Phase 4a CRUD (6 methods) + Phase 4b/4c list, search,
    and statistics methods. Wrapper methods (check_*, mark_*,
    get_music_data) inherit unchanged — they call into
    ``self.get_by_platform_id`` / ``self.update`` and Python MRO
    resolves those to the asyncpg overrides automatically."""

    TABLE = "parsed_media"

    # ── Core CRUD (Phase 4a) ────────────────────────────────────────

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            normalized = _normalize_for_pg(data)
            row = await self.insert(**normalized)
            logger.info(f"Created parsed_media: {data.get('platform_id')}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to create parsed_media: {e}")
            raise

    async def get_by_platform_id(self, platform_id: str) -> Optional[Dict[str, Any]]:
        """Lookup by external platform_id (text). Used by every
        parse + dedup probe — the most-called read on this table."""
        return await self.fetch_one(
            "SELECT * FROM parsed_media WHERE platform_id = $1 LIMIT 1",
            platform_id,
        )

    async def get_by_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        """Lookup by primary key. ``parsed_media.id`` is BIGINT
        (Snowflake — verified against information_schema, NOT UUID
        as some legacy docstrings claimed). API path params arrive
        as str → ``_bigint`` coercion required."""
        return await self.fetch_one(
            "SELECT * FROM parsed_media WHERE id = $1 LIMIT 1",
            self._bigint(media_id),
        )

    async def update(
        self, platform_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """UPDATE parsed_media by platform_id. Sets updated_at to now
        before binding (matches legacy)."""
        try:
            normalized = _normalize_for_pg(data)
            # asyncpg wants datetime objects directly — NOT isoformat
            # strings. See _normalize_for_pg docstring for the trap.
            normalized["updated_at"] = datetime.now()

            cols = list(normalized.keys())
            set_pairs = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(cols))
            sql = (
                f'UPDATE "parsed_media" SET {set_pairs} '
                f"WHERE platform_id = ${len(cols) + 1} RETURNING *"
            )
            row = await self.fetch_one(sql, *normalized.values(), platform_id)
            logger.info(f"Updated parsed_media: {platform_id}")
            return row
        except Exception as e:
            # Mirror legacy's verbose APIError unwrapping. asyncpg
            # exceptions don't have .message/.code/.details/.hint —
            # they put info in .args[0] and __dict__ — so we surface
            # whatever we can find. The shape stays close enough that
            # log-grepping by Bug D pattern still works.
            logger.error(
                f"Failed to update parsed_media platform_id={platform_id}: "
                f"type={type(e).__name__} repr={e!r} "
                f"args={getattr(e, 'args', None)!r} "
                f"detail={getattr(e, 'detail', None)!r} "
                f"sqlstate={getattr(e, 'sqlstate', None)!r}"
            )
            raise

    async def delete(self, platform_id: str) -> bool:
        try:
            await self.execute(
                "DELETE FROM parsed_media WHERE platform_id = $1",
                platform_id,
            )
            logger.info(f"Deleted parsed_media: {platform_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete parsed_media: {e}")
            return False

    async def get_downloaded_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Cross-user dedup probe — find a fully-downloaded record
        for this platform_id from ANY user. Same projection as
        legacy (id, download_path, storage_size, cover_download_path,
        source_platform, platform_id) — keep fields stable for callers."""
        return await self.fetch_one(
            "SELECT id, download_path, storage_size, cover_download_path, "
            "       source_platform, platform_id "
            "FROM parsed_media "
            "WHERE platform_id = $1 "
            "  AND video_download_status = $2 "
            "  AND download_path IS NOT NULL "
            "LIMIT 1",
            platform_id,
            DownloadStatus.COMPLETED.value,
        )

    # ── Bulk + list (Phase 4b) ──────────────────────────────────────

    async def mark_stale_downloads_failed(self, timeout_minutes: int = 30) -> int:
        """Mark downloads stuck in 'downloading' state as 'failed' on
        BOTH parsed_media (global) and resources (per-user).

        Returns the total count of status fields that were reset
        across both tables.

        Implementation note: the field name is spliced into the SQL
        because asyncpg can't parameterize identifiers — but the
        splice value comes from a closed module-level tuple
        (``_DOWNLOAD_STATUS_FIELDS``), NEVER from caller input, so
        injection is impossible.

        Datetimes are bound as ``datetime.datetime`` instances, NOT
        isoformat strings (legacy supabase-py wanted ISO; asyncpg's
        timestamp codec rejects str)."""
        cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
        now = datetime.now()
        count = 0
        err_msg = f"Download timed out (>{timeout_minutes}min)"

        for field in _DOWNLOAD_STATUS_FIELDS:
            # Clean parsed_media (global physical file state)
            try:
                rows = await self.fetch_all(
                    f'UPDATE "parsed_media" SET "{field}" = $1, '
                    f'"error_message" = $2, "updated_at" = $3 '
                    f'WHERE "{field}" = $4 AND "updated_at" < $5 '
                    f"RETURNING 1",
                    DownloadStatus.FAILED.value,
                    err_msg,
                    now,
                    "downloading",
                    cutoff,
                )
                count += len(rows)
            except Exception as e:
                logger.debug(f"Stale cleanup parsed_media.{field}: {e}")

            # Clean resources (per-user download state)
            try:
                rows = await self.fetch_all(
                    f'UPDATE "resources" SET "{field}" = $1, '
                    f'"updated_at" = $2 '
                    f'WHERE "{field}" = $3 AND "updated_at" < $4 '
                    f"RETURNING 1",
                    DownloadStatus.FAILED.value,
                    now,
                    "downloading",
                    cutoff,
                )
                count += len(rows)
            except Exception as e:
                logger.debug(f"Stale cleanup resources.{field}: {e}")

        if count > 0:
            logger.info(
                f"Marked {count} stale download(s) as failed "
                f"(timeout={timeout_minutes}min)"
            )
        return count

    async def get_pending_downloads(
        self,
        status: DownloadStatus = DownloadStatus.PENDING,
        limit: int = 100,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Pending downloads list. ``parsed_media`` is global; when
        ``user_id`` is provided, filter through ``resources`` so only
        the caller's items are returned.

        Uses an EXISTS subquery (cleaner than IN-list with array bind
        and lets PG short-circuit early). ``creator_id`` is UUID —
        bind str directly."""
        try:
            if user_id:
                return await self.fetch_all(
                    "SELECT pm.* FROM parsed_media pm "
                    "WHERE pm.video_download_status = $1 "
                    "  AND EXISTS ( "
                    "    SELECT 1 FROM resources r "
                    "    WHERE r.media_id = pm.id "
                    "      AND r.creator_id = $2 "
                    "      AND r.is_trashed = false "
                    "  ) "
                    "LIMIT $3",
                    status.value,
                    user_id,
                    limit,
                )
            return await self.fetch_all(
                "SELECT * FROM parsed_media "
                "WHERE video_download_status = $1 "
                "LIMIT $2",
                status.value,
                limit,
            )
        except Exception as e:
            logger.error(f"获取待下载列表失败: {e}")
            return []

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """Global parsed_media list — returns the CARD_SELECT projection
        (no heavy AI text fields). Use ``get_by_platform_id`` /
        ``get_by_id`` for the full record.

        ``order_by`` is whitelisted via ``_safe_order`` — unknown values
        fall back to ``created_at``."""
        try:
            col = _safe_order(order_by, _SAFE_PARSED_MEDIA_ORDER_COLS)
            direction = "ASC" if ascending else "DESC"
            sql = (
                f"SELECT {self.CARD_SELECT} FROM parsed_media "
                f'ORDER BY "{col}" {direction} '
                f"LIMIT $1 OFFSET $2"
            )
            return await self.fetch_all(sql, limit, skip)
        except Exception as e:
            logger.error(f"获取视频列表失败: {e}")
            return []

    async def get_user_media_list(
        self,
        user_id: str,
        skip: int = 0,
        limit: int = 100,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """Per-user media list. JOINs ``resources`` ⨝ ``parsed_media``
        and returns CARD_SELECT-shaped dicts with ``resource_id`` overlaid
        — preserves the embedded-PostgREST shape callers expect.

        ``order_by`` is on the ``resources`` row (not parsed_media) to
        match legacy semantics: list ordering follows when the user
        added the item to their library, not when the upstream platform
        published it."""
        try:
            col = _safe_order(order_by, _SAFE_RESOURCES_ORDER_COLS)
            direction = "ASC" if ascending else "DESC"
            # Project CARD_SELECT columns from parsed_media via "pm." prefix
            # so they collide-free join the resource_id column. The Python
            # post-process strips the qualifier — callers see the legacy
            # flat dict.
            card_cols = ", ".join(
                f"pm.{c.strip()}" for c in self.CARD_SELECT.split(",")
            )
            sql = (
                f"SELECT r.id AS __resource_id, {card_cols} "
                f"FROM resources r "
                f"INNER JOIN parsed_media pm ON pm.id = r.media_id "
                f"WHERE r.creator_id = $1 "
                f"  AND r.source_type = 'web' "
                f"  AND r.is_trashed = false "
                f'ORDER BY r."{col}" {direction} '
                f"LIMIT $2 OFFSET $3"
            )
            rows = await self.fetch_all(sql, user_id, limit, skip)
            videos = []
            for row in rows:
                resource_id = row.pop("__resource_id", None)
                row["resource_id"] = resource_id
                videos.append(row)
            return videos
        except Exception as e:
            logger.error(f"Failed to get user media list: {e}")
            return []

    # ── Search + statistics (Phase 4c) ──────────────────────────────

    async def search(
        self,
        user_id: str,
        keyword: Optional[str] = None,
        author: Optional[str] = None,
        status: Optional[DownloadStatus] = None,
        media_type: Optional[str] = None,
        category: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """User-scoped media search. Builds a parameterized WHERE clause
        from the optional filter args and JOINs ``resources`` ⨝
        ``parsed_media``. Return shape mirrors ``get_user_media_list``
        (parsed_media columns flat + ``resource_id`` overlay).

        ``category`` is accepted for signature parity but ignored —
        legacy never wired it to a WHERE clause either; tags live on a
        join table that this query doesn't touch.

        Datetimes are bound as ``datetime`` instances directly (legacy
        ``.isoformat()`` would have raised under asyncpg)."""
        try:
            card_cols = ", ".join(
                f"pm.{c.strip()}" for c in self.CARD_SELECT.split(",")
            )
            where = [
                "r.creator_id = $1",
                "r.source_type = 'web'",
                "r.is_trashed = false",
            ]
            args: List[Any] = [user_id]

            def _next() -> str:
                return f"${len(args) + 1}"

            if keyword:
                p = _next()
                where.append(f"(pm.title ILIKE {p} OR pm.description ILIKE {p})")
                args.append(f"%{keyword}%")
            if author:
                where.append(f"pm.author ILIKE {_next()}")
                args.append(f"%{author}%")
            if status:
                where.append(f"pm.video_download_status = {_next()}")
                args.append(status.value)
            if media_type:
                where.append(f"pm.media_type = {_next()}")
                args.append(media_type)
            if start_date:
                where.append(f"pm.published_at >= {_next()}")
                args.append(start_date)
            if end_date:
                where.append(f"pm.published_at <= {_next()}")
                args.append(end_date)

            limit_p = _next()
            args.append(limit)
            offset_p = _next()
            args.append(skip)

            sql = (
                f"SELECT r.id AS __resource_id, {card_cols} "
                f"FROM resources r "
                f"INNER JOIN parsed_media pm ON pm.id = r.media_id "
                f"WHERE {' AND '.join(where)} "
                f"ORDER BY r.created_at DESC "
                f"LIMIT {limit_p} OFFSET {offset_p}"
            )
            rows = await self.fetch_all(sql, *args)
            videos = []
            for row in rows:
                resource_id = row.pop("__resource_id", None)
                row["resource_id"] = resource_id
                videos.append(row)
            return videos
        except Exception as e:
            logger.error(f"搜索视频失败: {e}")
            return []

    async def get_statistics(self, user_id: str) -> Dict[str, Any]:
        """Per-user library statistics. Uses SQL aggregation
        (``COUNT FILTER``, ``SUM``, ``COUNT DISTINCT``) instead of
        materializing every row in Python — for users with thousands
        of items this avoids dragging the full result set across the
        Supavisor connection.

        ``skipped`` is computed in Python as a residual so the response
        shape matches legacy exactly (legacy formula:
        ``total - pending - completed - failed``)."""
        try:
            row = (
                await self.fetch_one(
                    "SELECT "
                    "  COUNT(*) AS total, "
                    "  COUNT(*) FILTER ("
                    "    WHERE pm.video_download_status = 'pending'"
                    "  ) AS pending, "
                    "  COUNT(*) FILTER ("
                    "    WHERE pm.video_download_status = 'completed'"
                    "  ) AS completed, "
                    "  COUNT(*) FILTER ("
                    "    WHERE pm.video_download_status = 'failed'"
                    "  ) AS failed, "
                    "  COALESCE(SUM(pm.datasize_bytes), 0) AS total_storage_bytes, "
                    "  COUNT(DISTINCT pm.author) "
                    "    FILTER (WHERE pm.author IS NOT NULL) AS unique_authors "
                    "FROM resources r "
                    "INNER JOIN parsed_media pm ON pm.id = r.media_id "
                    "WHERE r.creator_id = $1 "
                    "  AND r.source_type = 'web' "
                    "  AND r.is_trashed = false",
                    user_id,
                )
                or {}
            )
            total = int(row.get("total") or 0)
            pending = int(row.get("pending") or 0)
            completed = int(row.get("completed") or 0)
            failed = int(row.get("failed") or 0)
            return {
                "total": total,
                "pending": pending,
                "completed": completed,
                "failed": failed,
                "skipped": total - pending - completed - failed,
                "total_storage_bytes": int(row.get("total_storage_bytes") or 0),
                "unique_authors": int(row.get("unique_authors") or 0),
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {
                "total": 0,
                "pending": 0,
                "completed": 0,
                "failed": 0,
                "skipped": 0,
                "total_storage_bytes": 0,
                "unique_authors": 0,
            }


__all__ = ["MediaRepositoryAsyncpg"]
