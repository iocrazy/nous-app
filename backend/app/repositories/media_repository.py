# app/repositories/media_repository.py

"""Media Repository (SQLAlchemy 2.0 ORM, post-rollout).

Parsed-media (``parsed_media``) data-access layer. Prod runs 100% ORM, so
the per-domain ``USE_ORM_MEDIA`` flag and the standalone
``MediaRepositoryOrm`` twin have been retired (Task 5.1 → final cleanup):
``MediaRepository`` is now the single ORM-backed class and
``get_media_repository()`` returns it unconditionally. The class name and
every public signature are unchanged, so the ~65 call sites are zero-touch.

Pre-collapse, ``MediaRepositoryOrm(AsyncpgRepository, MediaRepository)``
overrode 12 data-access methods and inherited the 9 wrapper methods
(check_* / mark_* / get_music_data) via Python MRO. The collapse moves the
12 ORM bodies onto this class directly; the 9 wrappers still call
``self.get_by_platform_id`` / ``self.update`` and now resolve to the ORM
overrides on THIS class (no MRO indirection, identical behaviour). The two
owner-map helpers stay on the legacy supabase-py client (conscious-keep).

THE P0 FIX (preserved): ``update`` commits via ``write_scope()`` (which does
``session.begin()``), fixing the silent-rollback the asyncpg
``fetch_one("UPDATE…RETURNING")``-on-``connect()`` path had.

Fidelity contract (invisible swap):
  - dict at the boundary — never leak ORM ``ParsedMedia`` objects. Same dict
    shapes as the legacy impl (column projections, ``resource_id`` overlay,
    statistics keys). Reads funnel through ``_orm_obj_to_dict`` / ``_plain``
    so the ``metadata_``→``metadata`` rename resolves via the mapper (never
    ``getattr(obj, "metadata")``, which returns the MetaData registry) and
    ``Enum(DownloadStatus)`` status columns unwrap to bare ``str``.
  - ``_bigint()`` coercion (from ``AsyncpgRepository``) on str-snowflake ids
    before binding to BIGINT columns (asyncpg int8 codec is strict).
  - datetimes bound as ``datetime`` objects (tz-aware UTC), never isoformat.
  - enum normalization (DownloadStatus → ``.value``) on writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, or_, select, update

from app.core.enums import DownloadStatus
from app.db.pg_coerce import coerce_datetime_strings
from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.db.supabase_client import get_async_supabase_admin
from app.models import ParsedMedia, Resources
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict, _plain

# ParsedMedia DB-column-name → mapped-attribute-name. Built once from the
# mapper. For most columns name == key, but the JSONB ``metadata`` column is
# mapped to the Python attribute ``metadata_`` (SQLAlchemy reserves
# ``metadata`` on declarative classes for the MetaData registry), so reading
# ``getattr(obj, "metadata")`` would return the MetaData object, not the row
# value. Always resolve via this map (see ``_name_to_attr``).
_PM_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ParsedMedia)

# Whitelist of column names safe to use in ORDER BY. Anything outside the
# set falls back to ``created_at``. (The ORM ``order_by`` takes a column
# object, but we still validate the *name* the same way the asyncpg impl
# did so behaviour is identical for unknown/hostile input.)
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

# parsed_media columns that hold a download status enum. Closed set —
# never sourced from caller input.
_DOWNLOAD_STATUS_FIELDS = (
    "video_download_status",
    "music_download_status",
    "cover_download_status",
    "image_download_status",
)


def _normalize_for_pg(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce DownloadStatus enum values for the parsed_media table.

    Returns a NEW dict — caller's input is not mutated (immutability rule).
    The asyncpg timestamp codec expects ``datetime.datetime`` instances, NOT
    isoformat strings — call sites should bind datetimes, and any ISO string
    that slips through is parsed here (``coerce_datetime_strings``) instead
    of rolling back the whole UPDATE (the 2026-07-05 download_path incident).

    Also remaps the DB column name ``metadata`` (callers pass raw column
    names) to the model attribute ``metadata_`` so ORM ``.values()`` —
    which keys on attribute names — accepts it."""
    out = coerce_datetime_strings(ParsedMedia, data)
    for field in _DOWNLOAD_STATUS_FIELDS:
        v = out.get(field)
        if isinstance(v, DownloadStatus):
            out[field] = v.value
    if "metadata" in out:
        out["metadata_"] = out.pop("metadata")
    return out


def _safe_order(order_by: str, allowed: set, default: str = "created_at") -> str:
    """Validate ``order_by`` against an allow-list. Unknown values fall
    back to ``default`` — neutralises a hostile column name."""
    return order_by if order_by in allowed else default


def _pm_card_dict(row: Any) -> Dict[str, Any]:
    """Build a CARD_SELECT-shaped plain dict from a ParsedMedia ORM row.

    Returns ONLY the CARD_SELECT columns (the projection the legacy
    ``get_all`` / ``get_user_media_list`` / ``search`` returned), never the
    heavy AI text blobs and never the live ORM object. Enum-typed status
    columns are unwrapped to bare strings (see ``_plain``)."""
    return {col: _plain(getattr(row, col)) for col in _CARD_COLS}


class MediaRepository(AsyncpgRepository):
    """Media Repository for the ``parsed_media`` table (async, ORM-backed).

    The 12 data-access methods run on the ORM session scopes. The 9 wrapper
    methods (check_* / mark_* / get_music_data) call ``self.get_by_platform_id``
    / ``self.update`` and resolve to the ORM methods on this class. The two
    owner-map helpers are conscious-kept legacy supabase-py bodies — they still
    use ``self._get_client()``. ``_bigint`` comes from ``AsyncpgRepository``."""

    TABLE = "parsed_media"
    TABLE_NAME = "parsed_media"

    # Card-view projection. Everything the library grid / feed / search cards
    # need, MINUS the heavyweight text blobs (``ai_extract_text`` /
    # ``ai_rewrite_text`` / ``ai_analyze_text`` / ``ai_generated_at``).
    # Detail endpoints (``get_by_platform_id`` / ``get_by_id``) still return
    # ``SELECT *`` so the PlayerPage sees the full record when the user
    # actually opens an item.
    #
    # CRITICAL: every column name here MUST exist on ``parsed_media``
    # verbatim. If a column is misspelled or belongs to another table
    # (e.g. AI statuses live on ``resources``, tags live on a join table),
    # PostgREST rejects the whole query with a 400 and the list / search
    # endpoints return empty. Verified against information_schema on
    # 2026-04-24.
    CARD_SELECT = (
        "id, platform_id, source_platform, "
        "title, author, description, "
        "original_url, "
        "cover_urls, dynamic_cover_url, cover_download_status, cover_download_path, "
        "like_count, comment_count, share_count, favorite_count, view_count, "
        "extract_audio_path, music_download_path, music_download_status, music_name, music_play_urls, "
        "video_download_status, video_download_urls, "
        "image_download_status, image_download_urls, image_download_path, "
        "hashtags, error_message, "
        "created_at, updated_at, published_at, last_viewed_at, "
        "media_type, media_format, duration, resolution, "
        "datasize, datasize_bytes, storage_size, keep_forever, "
        "hls_path, download_path, download_time, download_duration"
    )

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers).

        Retained for the two owner-map helpers below, which stay on the legacy
        supabase-py REST path (conscious-keep)."""
        return await get_async_supabase_admin()

    # ── Core CRUD ───────────────────────────────────────────────────

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """INSERT a parsed_media row (committed via write_scope) and
        return the inserted record as a plain dict."""
        try:
            normalized = _normalize_for_pg(data)
            async with write_scope() as session:
                result = await session.execute(
                    insert(ParsedMedia)
                    .values(**normalized)
                    .returning(*ParsedMedia.__table__.columns)
                )
                row = result.mappings().first()
                created = dict(row) if row else {}
            logger.info(f"Created parsed_media: {data.get('platform_id')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create parsed_media: {e}")
            raise

    async def get_by_platform_id(self, platform_id: str) -> Optional[Dict[str, Any]]:
        """Lookup by external platform_id (text). Most-called read."""
        async with read_scope() as session:
            result = await session.execute(
                select(ParsedMedia)
                .where(ParsedMedia.platform_id == platform_id)
                .limit(1)
            )
            row = result.scalars().first()
            return _orm_obj_to_dict(row, _PM_NAME_TO_ATTR) if row else None

    async def get_by_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        """Lookup by primary key. ``parsed_media.id`` is BIGINT (Snowflake).
        API path params arrive as str → ``_bigint`` coercion required."""
        async with read_scope() as session:
            result = await session.execute(
                select(ParsedMedia)
                .where(ParsedMedia.id == self._bigint(media_id))
                .limit(1)
            )
            row = result.scalars().first()
            return _orm_obj_to_dict(row, _PM_NAME_TO_ATTR) if row else None

    async def update(
        self, platform_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """UPDATE parsed_media by platform_id, COMMITTING via write_scope.

        THIS IS THE P0 FIX. The asyncpg path ran the UPDATE on a bare
        ``connect()`` (no txn) and silently rolled back on close. Here the
        write_scope() ``session.begin()`` block commits, so the change
        persists.

        Sets ``updated_at`` to now (tz-aware UTC) before binding — matches
        the legacy + asyncpg behaviour. parsed_media DOES have a BEFORE-UPDATE
        trigger (``update_videos_updated_at``) that also stamps updated_at =
        now(), so the explicit set here is redundant with the trigger and
        merely preserves byte-for-byte parity with the prior impls (which all
        set it client-side). Per the route-C discipline, trigger-owned columns
        ideally aren't set Python-side; the cleaner change is to drop this line
        and let the trigger own it. Kept set for now only to match prior
        behaviour — recommend removing in a follow-up once callers are
        confirmed not to depend on the client clock value."""
        try:
            normalized = _normalize_for_pg(data)
            normalized["updated_at"] = datetime.now(timezone.utc)

            async with write_scope() as session:
                result = await session.execute(
                    update(ParsedMedia)
                    .where(ParsedMedia.platform_id == platform_id)
                    .values(**normalized)
                    .returning(*ParsedMedia.__table__.columns)
                )
                row = result.mappings().first()
                updated = dict(row) if row else None
            logger.info(f"Updated parsed_media: {platform_id}")
            return updated
        except Exception as e:
            # Mirror legacy's verbose error unwrapping so log-grepping by
            # the Bug D pattern still works.
            logger.error(
                f"Failed to update parsed_media platform_id={platform_id}: "
                f"type={type(e).__name__} repr={e!r} "
                f"args={getattr(e, 'args', None)!r} "
                f"detail={getattr(e, 'detail', None)!r} "
                f"sqlstate={getattr(e, 'sqlstate', None)!r}"
            )
            raise

    async def delete(self, platform_id: str) -> bool:
        """DELETE parsed_media by platform_id (committed via write_scope)."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ParsedMedia).where(ParsedMedia.platform_id == platform_id)
                )
            logger.info(f"Deleted parsed_media: {platform_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete parsed_media: {e}")
            return False

    async def get_downloaded_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Cross-user dedup probe — find a fully-downloaded record for this
        platform_id from ANY user. Same projection as legacy (id,
        download_path, storage_size, cover_download_path, source_platform,
        platform_id)."""
        async with read_scope() as session:
            result = await session.execute(
                select(
                    ParsedMedia.id,
                    ParsedMedia.download_path,
                    ParsedMedia.storage_size,
                    ParsedMedia.cover_download_path,
                    ParsedMedia.source_platform,
                    ParsedMedia.platform_id,
                )
                .where(ParsedMedia.platform_id == platform_id)
                .where(
                    ParsedMedia.video_download_status == DownloadStatus.COMPLETED.value
                )
                .where(ParsedMedia.download_path.isnot(None))
                .limit(1)
            )
            row = result.mappings().first()
            return dict(row) if row else None

    # ── Bulk + list ─────────────────────────────────────────────────

    async def mark_stale_downloads_failed(self, timeout_minutes: int = 30) -> int:
        """Mark downloads stuck in 'downloading' state as 'failed' on
        parsed_media (the canonical global physical-file state). Returns the
        total count of status fields reset.

        Download statuses live ONLY on parsed_media in the route-C schema;
        ``resources`` has no ``*_download_status`` columns. The legacy
        supabase-py base already dropped the resources sweep for that reason
        (it 400'd on every run — pure noise), so this impl does NOT attempt
        it either: an UPDATE against ``Resources.<field>`` would raise
        AttributeError at statement-build time and never touch the DB. The
        committing write_scope() is the fix vs the silent-rollback connect()
        path.

        Datetimes bound as tz-aware ``datetime`` instances."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        now = datetime.now(timezone.utc)
        count = 0
        err_msg = f"Download timed out (>{timeout_minutes}min)"

        for field in _DOWNLOAD_STATUS_FIELDS:
            # Clean parsed_media (global physical file state).
            try:
                async with write_scope() as session:
                    result = await session.execute(
                        update(ParsedMedia)
                        .where(getattr(ParsedMedia, field) == "downloading")
                        .where(ParsedMedia.updated_at < cutoff)
                        .values(
                            **{field: DownloadStatus.FAILED.value},
                            error_message=err_msg,
                            updated_at=now,
                        )
                    )
                    count += result.rowcount or 0
            except Exception as e:
                logger.debug(f"Stale cleanup parsed_media.{field}: {e}")

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
        """Pending downloads list. parsed_media is global; when ``user_id``
        is given, filter through ``resources`` (EXISTS subquery) so only the
        caller's items are returned. Returns full-row dicts (SELECT *)."""
        try:
            async with read_scope() as session:
                stmt = select(ParsedMedia).where(
                    ParsedMedia.video_download_status == status.value
                )
                if user_id:
                    exists_subq = (
                        select(Resources.id)
                        .where(Resources.media_id == ParsedMedia.id)
                        .where(Resources.creator_id == user_id)
                        .where(Resources.is_trashed.is_(False))
                        .exists()
                    )
                    stmt = stmt.where(exists_subq)
                stmt = stmt.limit(limit)
                result = await session.execute(stmt)
                return [
                    _orm_obj_to_dict(r, _PM_NAME_TO_ATTR)
                    for r in result.scalars().all()
                ]
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
        """Global parsed_media list — CARD_SELECT projection (no heavy AI
        text fields). ``order_by`` whitelisted; unknown → created_at."""
        try:
            col_name = _safe_order(order_by, _SAFE_PARSED_MEDIA_ORDER_COLS)
            col = getattr(ParsedMedia, col_name)
            order_clause = col.asc() if ascending else col.desc()
            async with read_scope() as session:
                result = await session.execute(
                    select(ParsedMedia).order_by(order_clause).limit(limit).offset(skip)
                )
                return [_pm_card_dict(r) for r in result.scalars().all()]
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
        """Per-user media list. JOINs resources ⨝ parsed_media and returns
        CARD_SELECT-shaped dicts with ``resource_id`` overlaid — preserves
        the embedded-PostgREST shape callers expect. Ordering is on the
        ``resources`` row (library add-time), matching legacy semantics."""
        try:
            col_name = _safe_order(order_by, _SAFE_RESOURCES_ORDER_COLS)
            col = getattr(Resources, col_name)
            order_clause = col.asc() if ascending else col.desc()
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources.id.label("__resource_id"), ParsedMedia)
                    .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
                    .where(Resources.creator_id == user_id)
                    .where(Resources.source_type == "web")
                    .where(Resources.is_trashed.is_(False))
                    .order_by(order_clause)
                    .limit(limit)
                    .offset(skip)
                )
                videos: List[Dict[str, Any]] = []
                for resource_id, media in result.all():
                    card = _pm_card_dict(media)
                    card["resource_id"] = resource_id
                    videos.append(card)
                return videos
        except Exception as e:
            logger.error(f"Failed to get user media list: {e}")
            return []

    # ── Search + statistics ─────────────────────────────────────────

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
        """User-scoped media search. JOINs resources ⨝ parsed_media and
        builds a parameterised WHERE from the optional filters. Same return
        shape as ``get_user_media_list`` (CARD_SELECT columns + resource_id).

        ``category`` is accepted for signature parity but ignored — legacy
        never wired it to a WHERE clause (tags live on a join table this
        query doesn't touch). Datetimes bound as ``datetime`` directly."""
        try:
            stmt = (
                select(Resources.id.label("__resource_id"), ParsedMedia)
                .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
                .where(Resources.creator_id == user_id)
                .where(Resources.source_type == "web")
                .where(Resources.is_trashed.is_(False))
            )

            if keyword:
                pattern = f"%{keyword}%"
                stmt = stmt.where(
                    or_(
                        ParsedMedia.title.ilike(pattern),
                        ParsedMedia.description.ilike(pattern),
                    )
                )
            if author:
                stmt = stmt.where(ParsedMedia.author.ilike(f"%{author}%"))
            if status:
                stmt = stmt.where(ParsedMedia.video_download_status == status.value)
            if media_type:
                stmt = stmt.where(ParsedMedia.media_type == media_type)
            if start_date:
                stmt = stmt.where(ParsedMedia.published_at >= start_date)
            if end_date:
                stmt = stmt.where(ParsedMedia.published_at <= end_date)

            stmt = stmt.order_by(Resources.created_at.desc()).limit(limit).offset(skip)

            async with read_scope() as session:
                result = await session.execute(stmt)
                videos: List[Dict[str, Any]] = []
                for resource_id, media in result.all():
                    card = _pm_card_dict(media)
                    card["resource_id"] = resource_id
                    videos.append(card)
                return videos
        except Exception as e:
            logger.error(f"搜索视频失败: {e}")
            return []

    async def get_statistics(self, user_id: str) -> Dict[str, Any]:
        """Per-user library statistics via SQL aggregation (COUNT FILTER,
        SUM, COUNT DISTINCT). ``skipped`` computed in Python as the residual
        so the response shape matches legacy exactly."""
        try:
            pending_status = DownloadStatus.PENDING.value
            completed_status = DownloadStatus.COMPLETED.value
            failed_status = DownloadStatus.FAILED.value
            stmt = (
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(ParsedMedia.video_download_status == pending_status)
                    .label("pending"),
                    func.count()
                    .filter(ParsedMedia.video_download_status == completed_status)
                    .label("completed"),
                    func.count()
                    .filter(ParsedMedia.video_download_status == failed_status)
                    .label("failed"),
                    func.coalesce(func.sum(ParsedMedia.datasize_bytes), 0).label(
                        "total_storage_bytes"
                    ),
                    func.count(func.distinct(ParsedMedia.author))
                    .filter(ParsedMedia.author.isnot(None))
                    .label("unique_authors"),
                )
                .select_from(Resources)
                .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
                .where(Resources.creator_id == user_id)
                .where(Resources.source_type == "web")
                .where(Resources.is_trashed.is_(False))
            )
            async with read_scope() as session:
                result = await session.execute(stmt)
                row = result.mappings().first() or {}

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

    # ── Wrapper methods (route through the ORM overrides above) ──────

    async def check_media_existence(self, platform_id: str) -> bool:
        """检查媒体是否存在"""
        result = await self.get_by_platform_id(platform_id)
        return result is not None

    async def check_media_downloaded(self, platform_id: str) -> bool:
        """检查媒体是否已下载"""
        result = await self.get_by_platform_id(platform_id)
        if result:
            return result.get("video_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def check_music_downloaded(self, platform_id: str) -> bool:
        """检查音乐是否已下载"""
        result = await self.get_by_platform_id(platform_id)
        if result:
            return result.get("music_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def check_cover_downloaded(self, platform_id: str) -> bool:
        """检查封面是否已下载"""
        result = await self.get_by_platform_id(platform_id)
        if result:
            return result.get("cover_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def mark_media_as_downloaded(
        self,
        platform_id: str,
        download_path: str,
        duration: float,
        storage_size: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """标记视频为已下载"""
        data = {
            "video_download_status": DownloadStatus.COMPLETED.value,
            "download_path": download_path,
            "download_duration": duration,
            # datetime OBJECT, not .isoformat() — a string here made asyncpg
            # roll back the whole UPDATE (download_path included), which is
            # how downloads silently stopped registering on 2026-07-05.
            "download_time": datetime.now(timezone.utc),
        }
        if storage_size > 0:
            data["storage_size"] = storage_size
            data["datasize_bytes"] = storage_size
        return await self.update(platform_id, data)

    async def mark_music_as_downloaded(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """标记音乐为已下载"""
        return await self.update(
            platform_id, {"music_download_status": DownloadStatus.COMPLETED.value}
        )

    async def mark_images_as_downloaded(
        self, platform_id: str, download_path: str, duration: float
    ) -> Optional[Dict[str, Any]]:
        """Mark image carousel as downloaded (uses image_download_status)."""
        return await self.update(
            platform_id,
            {
                "image_download_status": DownloadStatus.COMPLETED.value,
                "image_download_path": download_path,
                "download_duration": duration,
                "download_time": datetime.now(timezone.utc),
            },
        )

    async def get_music_data(self, platform_id: str) -> Dict[str, Any]:
        """
        获取音乐下载所需数据

        Args:
            platform_id: 视频唯一标识

        Returns:
            包含音乐URL和名称的字典

        Raises:
            ValueError: 如果找不到视频记录
        """
        result = await self.get_by_platform_id(platform_id)
        if not result:
            raise ValueError(f"找不到视频数据: {platform_id}")
        return {
            "id": result.get("id"),
            "source_platform": result.get("source_platform"),
            "music_name": result.get("music_name"),
        }

    async def mark_download_failed(
        self, platform_id: str, error_message: str, is_video: bool = True
    ) -> Optional[Dict[str, Any]]:
        """标记下载失败"""
        status_field = "video_download_status" if is_video else "music_download_status"
        return await self.update(
            platform_id,
            {status_field: DownloadStatus.FAILED.value, "error_message": error_message},
        )

    # ── Owner maps (conscious-keep legacy supabase-py REST bodies) ───

    async def get_media_owner_map(self, media_ids: List[Any]) -> Dict[str, str]:
        """Map parsed_media.id -> resources.creator_id (the download owner).

        parsed_media is a global table with no user_id column (dropped in the
        scope-1 refactor); ownership lives in resources.creator_id, joined via
        resources.media_id -> parsed_media.id. Returns {media_id_str:
        creator_id_str} for non-trashed resources only. A media id with no
        backing resource is omitted (genuine orphan: legacy/system download)
        so callers can skip it.
        """
        if not media_ids:
            return {}
        try:
            client = await self._get_client()
            res = (
                await client.table("resources")
                .select("media_id, creator_id")
                .in_("media_id", [str(m) for m in media_ids])
                .eq("is_trashed", False)
                .execute()
            )
            owner_map: Dict[str, str] = {}
            for row in res.data or []:
                mid = row.get("media_id")
                cid = row.get("creator_id")
                if mid is not None and cid and str(mid) not in owner_map:
                    owner_map[str(mid)] = str(cid)
            return owner_map
        except Exception as e:
            logger.error(f"get_media_owner_map failed: {e}")
            return {}

    async def get_media_resource_owner_map(
        self, media_ids: List[Any]
    ) -> Dict[str, Dict[str, str]]:
        """Like get_media_owner_map but also returns the resource_id.

        The download-retry path (scheduled_recovery) re-dispatches
        download_workflow; without resource_id the chain_followups_step
        (extract_audio / thumbnail / transcode / AI) early-returns, so a
        successfully-retried download never gets its audio/AI. Returns
        ``{media_id_str: {"user_id": creator_id, "resource_id": resource_id}}``
        for non-trashed resources (orphans omitted, same as
        get_media_owner_map).
        """
        if not media_ids:
            return {}
        try:
            client = await self._get_client()
            res = (
                await client.table("resources")
                .select("id, media_id, creator_id")
                .in_("media_id", [str(m) for m in media_ids])
                .eq("is_trashed", False)
                .execute()
            )
            out: Dict[str, Dict[str, str]] = {}
            for row in res.data or []:
                mid = row.get("media_id")
                cid = row.get("creator_id")
                rid = row.get("id")
                if mid is not None and cid and str(mid) not in out:
                    out[str(mid)] = {
                        "user_id": str(cid),
                        "resource_id": str(rid) if rid is not None else "",
                    }
            return out
        except Exception as e:
            logger.error(f"get_media_resource_owner_map failed: {e}")
            return {}


# CARD_SELECT column names, parsed once from the projection string so
# ``_pm_card_dict`` selects the SAME columns the legacy impl returned.
_CARD_COLS: List[str] = [c.strip() for c in MediaRepository.CARD_SELECT.split(",")]


# ─── Repository factory (post-rollout, ORM-only) ───────────────────────
#
# The per-domain ``USE_ORM_MEDIA`` flag has been retired — prod runs 100%
# ORM. ``get_media_repository()`` unconditionally returns the (now ORM-backed)
# ``MediaRepository``. Call sites that construct ``MediaRepository()`` directly
# get the same ORM implementation.


def get_media_repository() -> MediaRepository:
    """Return the media repository (ORM-backed, post-rollout)."""
    return MediaRepository()
