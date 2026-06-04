"""SQLAlchemy 2.0 ORM implementation of MediaRepository (Task 5.1).

The successor to ``media_repository_asyncpg.py``. Same Strangler Fig
multiple-inheritance pattern (overrides the 12 data-access methods,
inherits the 9 wrapper methods — check_* / mark_* / get_music_data —
from the legacy supabase-py base via Python MRO), but the internals run
on the ORM session scopes from ``app.db.session`` instead of
``db_engine.fetch_one``/``execute``.

THE P0 FIX
==========
The asyncpg ``update`` ran ``self.fetch_one("UPDATE … RETURNING *")``,
which opened ``engine.connect()`` (NO transaction). On connection close
the UPDATE **silently rolled back** — the returned row was the in-memory
RETURNING buffer that was never committed, so the next read saw the OLD
value (silent data loss). This implementation routes every write through
``write_scope()`` which does ``session.begin()`` and COMMITS, so the
change actually persists. The P0-persistence regression test in
``tests/integration/test_media_repository_orm.py`` pins this.

Fidelity contract (the swap must be invisible to 65 callers):
  - dict at the boundary — never leak ORM ``ParsedMedia`` objects.
    Same exact dict shapes as the asyncpg impl (column projections,
    ``resource_id`` overlay, statistics keys).
  - ``_bigint()`` coercion on str-snowflake ids before binding to
    BIGINT columns (asyncpg int8 codec is strict).
  - datetimes bound as ``datetime`` objects (tz-aware UTC), never
    isoformat strings.
  - enum normalization (DownloadStatus → ``.value``) on writes.

Idempotency (for the controller's DBOS-step validation step):
  - ``create``    — INSERT, NOT idempotent by itself (re-run inserts a
                    duplicate row), but downloader dedup guards it.
  - ``update``    — SET-to-fixed-values, naturally idempotent.
  - ``delete``    — DELETE by platform_id, idempotent.
  - ``mark_stale_downloads_failed`` — SET status='failed' WHERE
                    status='downloading'; re-run is a no-op (idempotent).
  None of the media write methods do an INCREMENT, so all writes are
  safe to retry under DBOS.
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, inspect, or_, select, update

from app.core.enums import DownloadStatus
from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.models import ParsedMedia, Resources
from app.repositories.media_repository import MediaRepository

# ParsedMedia DB-column-name → mapped-attribute-name. Built once from the
# mapper. For most columns name == key, but the JSONB ``metadata`` column is
# mapped to the Python attribute ``metadata_`` (SQLAlchemy reserves
# ``metadata`` on declarative classes for the MetaData registry), so reading
# ``getattr(obj, "metadata")`` would return the MetaData object, not the row
# value. Always resolve via this map.
_PM_NAME_TO_ATTR: Dict[str, str] = {
    prop.columns[0].name: prop.key
    for prop in inspect(ParsedMedia).column_attrs
}

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

# CARD_SELECT column names, parsed once from the legacy projection string
# so the ORM read selects the SAME columns the asyncpg/legacy impls return.
_CARD_COLS: List[str] = [c.strip() for c in MediaRepository.CARD_SELECT.split(",")]


def _normalize_for_pg(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce DownloadStatus enum values for the parsed_media table.

    Returns a NEW dict — caller's input is not mutated (immutability rule).
    Datetimes are passed through AS-IS — the asyncpg driver's timestamp
    codec expects ``datetime.datetime`` instances, NOT isoformat strings.

    Also remaps the DB column name ``metadata`` (callers pass raw column
    names) to the model attribute ``metadata_`` so ORM ``.values()`` —
    which keys on attribute names — accepts it."""
    out = dict(data)
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


def _plain(value: Any) -> Any:
    """Coerce a value to its plain-Python form at the read dict boundary.

    The ORM types the 5 ``*_download_status`` columns as ``Enum(DownloadStatus)``
    so reads return ``DownloadStatus`` members, whereas the retired asyncpg /
    legacy supabase-py impls returned bare ``str``. Enum members ARE str
    subclasses, so ``==`` and ``json.dumps`` look fine — but ``str(x)`` /
    f-strings yield ``"DownloadStatus.COMPLETED"`` instead of ``"completed"``,
    silently breaking parity for the 65 callers. Unwrap any Enum to ``.value``
    so every status field returns exactly the bare string the prior impls did."""
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _pm_card_dict(row: Any) -> Dict[str, Any]:
    """Build a CARD_SELECT-shaped plain dict from a ParsedMedia ORM row.

    Returns ONLY the CARD_SELECT columns (the projection the asyncpg
    ``get_all`` / ``get_user_media_list`` / ``search`` returned), never
    the heavy AI text blobs and never the live ORM object. Enum-typed
    status columns are unwrapped to bare strings (see ``_plain``)."""
    return {col: _plain(getattr(row, col)) for col in _CARD_COLS}


class MediaRepositoryOrm(AsyncpgRepository, MediaRepository):
    """ORM-backed MediaRepository for the ``parsed_media`` table.

    Overrides the 12 data-access methods; the 9 wrapper methods
    (check_* / mark_* / get_music_data) inherit unchanged and resolve
    ``self.get_by_platform_id`` / ``self.update`` to these overrides via
    MRO. ``_bigint`` is inherited from AsyncpgRepository."""

    TABLE = "parsed_media"

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
            return _orm_obj_to_dict(row) if row else None

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
            return _orm_obj_to_dict(row) if row else None

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
                return [_orm_obj_to_dict(r) for r in result.scalars().all()]
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


def _orm_obj_to_dict(obj: Any) -> Dict[str, Any]:
    """Convert a full ParsedMedia ORM object to a plain dict keyed by DB
    column NAME (SELECT * parity).

    The dict is keyed by DB column name but the *value* must be read via the
    mapped Python attribute, which is NOT always the column name: the JSONB
    ``metadata`` column is mapped to the attribute ``metadata_`` (SQLAlchemy
    reserves ``metadata`` on declarative classes for the MetaData registry).
    Reading ``getattr(obj, "metadata")`` would hand back the MetaData object,
    not the row value — so we resolve the attribute name via ``_PM_NAME_TO_ATTR``.
    Enum-typed status columns are unwrapped to bare strings (see ``_plain``) so
    the dict matches the legacy supabase-py / asyncpg SELECT * shape exactly."""
    out: Dict[str, Any] = {}
    for name, attr in _PM_NAME_TO_ATTR.items():
        out[name] = _plain(getattr(obj, attr))
    return out


__all__ = ["MediaRepositoryOrm"]
