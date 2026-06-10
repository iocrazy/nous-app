"""SQLAlchemy 2.0 ORM implementation of AdminTranscodeRepository (Phase 2 admin wave).

REST → ORM successor for the HLS-transcode admin console
(``app/api/admin/transcode_router.py``), which reads ``resource_versions`` /
``resources`` / ``parsed_media`` and reads+WRITES ``system_settings``.
``AdminTranscodeRepositoryOrm`` subclasses ``AdminTranscodeRepository`` and overrides
every method; the ``*_TABLE`` / ``VALID_SORT_FIELDS`` / ``LIST_COLUMNS`` constants
are inherited. Call sites route through ``get_admin_transcode_repository()`` (bottom
of ``transcode_repository.py``).

MODELS (all verified reflected + exported from ``app.models``):
  - ``ResourceVersions`` (table ``resource_versions``) — PK ``id`` BIGINT.
  - ``Resources``        (table ``resources``)         — PK ``id`` BIGINT;
    ``media_id`` BIGINT.
  - ``ParsedMedia``      (table ``parsed_media``)      — PK ``id`` BIGINT.
  - ``SystemSettings``   (table ``system_settings``)   — PK ``key`` text;
    ``value`` JSONB.

★ UUID AUDIT — NO load-bearing uuid in any consumed path ★
==========================================================
Every id/FK this repo reads is BIGINT (resource_versions.id / .resource_id;
resources.id / .media_id; parsed_media.id), and system_settings is keyed by a TEXT
``key``. Per-column dict-key/compare evidence (from transcode_router.py):

  - ``resource_versions.id`` (BIGINT) → **native int**. Router str()s it
    (``id=str(row["id"])``, ``vid = str(v["id"])``). ``str(native_int)`` round-trips.
  - ``resource_versions.resource_id`` (BIGINT) → **native int**. DICT-KEY EVIDENCE:
    ``resource_ids = list({r["resource_id"] for r in rows})`` (SET) → fed to
    ``resources_to_media`` + later ``media_info_map.get(str(r["resource_id"]))``.
    The maps key by ``str(...)``; ``str(native_int)`` round-trips, so it STAYS int.
  - ``resources.id`` / ``resources.media_id`` / ``parsed_media.id`` (BIGINT) →
    **native int**, str()'d into the returned maps (``{str(r["id"]): str(r["media_id"])}``
    / ``{str(m["id"]): m}``) — exact REST shape.
  - The ONLY uuid columns on these tables (``resource_versions.uploaded_by``,
    ``resources.creator_id``, ``system_settings.updated_by``) are NEVER selected by
    any method here (LIST_COLUMNS + the explicit projections exclude them). So uuid
    coercion is NOT load-bearing in this repo; the defensive ``_parity`` sweep
    (uuid→str) is kept for safety but is a no-op on every projection.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at / transcode_at (resource_versions, timestamptz) → **ISO str** (CONSUMED:
    ``AdminTranscodeVersionResponse.created_at`` / ``transcode_at`` read
    ``row.get("created_at")`` / ``row.get("transcode_at")``). file_size_bytes (bigint)
    / version_number (int) → native int. transcode_status / mime_type / filename /
    hls_path (text) → native str (transcode_status is a plain ``String(20)`` with a
    comment, NOT a SQLAlchemy Enum → no ``_plain`` needed). count_* → exact COUNT →
    native int (the 5.3 trap). cover_urls (parsed_media, jsonb) → native list/dict
    (router does ``(media.get("cover_urls") or [None])[0]``). title / author /
    source_platform / cover_download_path → native str.

NUMERIC AUDIT: no cost/duration NUMERIC columns are SELECTED by this repo
(``resource_versions.duration_seconds`` is Integer and NOT in LIST_COLUMNS;
``resource_versions.confidence`` does not exist — that's on resource_tags). So no
NUMERIC→Decimal parity concern arises here.

LIST FILTERS / SEARCH (reproduced exactly)
------------------------------------------
  ``mime_type LIKE 'video/%'`` on every versions query. status_filter: "null" →
    ``transcode_status IS NULL``; else eq. min_size_mb>0 → ``file_size_bytes >=
    min_size_mb*1024*1024``. sort_by validated vs VALID_SORT_FIELDS (else
    "created_at"), desc/asc. Paginated via offset/limit. count_by_status / batch use
    eq / IS NULL the same way. NO date-range filter exists → no timestamptz<VARCHAR
    hazard.

WRITES (the silent-rollback P0 lesson) — ALL commit via write_scope()
---------------------------------------------------------------------
  mark_pending(version_id) → UPDATE resource_versions SET transcode_status='pending'
    WHERE id=… ; upsert_setting(key, value, updated_by) → pg_insert ON CONFLICT
    (key) DO UPDATE SET value, updated_by (reproduces the supabase ``.upsert`` on the
    ``key`` PK). PHANTOM SCREEN: key / value / updated_by are all real
    system_settings columns. Both commit via write_scope().
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import ParsedMedia, Resources, ResourceVersions, SystemSettings
from app.repositories.admin.transcode_repository import (
    BATCH_VERSIONS_LIMIT,
    AdminTranscodeRepository,
)

# Ordered (result-dict KEY, ORM attribute) for the LIST_COLUMNS projection.
_LIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "id"),
    ("resource_id", "resource_id"),
    ("version_number", "version_number"),
    ("filename", "filename"),
    ("file_size_bytes", "file_size_bytes"),
    ("mime_type", "mime_type"),
    ("transcode_status", "transcode_status"),
    ("hls_path", "hls_path"),
    ("transcode_at", "transcode_at"),
    ("created_at", "created_at"),
)


def _bigint(value: Any) -> int:
    """Coerce a snowflake id (version_id / resource_id / media_id) to a native int
    for a BIGINT bind. asyncpg's int8 codec is STRICT — ids arrive as STR (path
    params + the str-keyed maps the router builds) but the legacy PostgREST path
    silently coerced them; we int-coerce at every bigint .eq/.in_ bind."""
    if isinstance(value, int):
        return value
    return int(str(value))


def _parity(value: Any) -> Any:
    """Strategy-C read coercion: datetime → ISO str; uuid → str (defensive no-op —
    no uuid is selected here). bigint/int/text/jsonb pass through unchanged."""
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _version_row(obj: Any) -> Dict[str, Any]:
    """Build the LIST_COLUMNS-shaped dict from a ResourceVersions row."""
    return {key: _parity(getattr(obj, attr)) for key, attr in _LIST_FIELDS}


class AdminTranscodeRepositoryOrm(AdminTranscodeRepository):
    """ORM-backed AdminTranscodeRepository (HLS transcode admin reads + writes)."""

    # ─── Stats ─────────────────────────────────────────────────────────

    async def count_total_video_versions(self) -> int:
        stmt = (
            select(func.count())
            .select_from(ResourceVersions)
            .where(ResourceVersions.mime_type.like("video/%"))
        )
        async with read_scope() as session:
            total = await session.scalar(stmt)
        return total or 0

    async def count_by_status(self, status: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ResourceVersions)
            .where(
                ResourceVersions.mime_type.like("video/%"),
                ResourceVersions.transcode_status == status,
            )
        )
        async with read_scope() as session:
            total = await session.scalar(stmt)
        return total or 0

    async def status_counts(self, statuses: list[str]) -> dict[str, int]:
        results = await asyncio.gather(*[self.count_by_status(s) for s in statuses])
        return dict(zip(statuses, results))

    # ─── List ──────────────────────────────────────────────────────────

    async def list_video_versions(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: Optional[str] = None,
        min_size_mb: Optional[int] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(ResourceVersions).where(
            ResourceVersions.mime_type.like("video/%")
        )

        if status_filter == "null":
            base = base.where(ResourceVersions.transcode_status.is_(None))
        elif status_filter:
            base = base.where(ResourceVersions.transcode_status == status_filter)

        if min_size_mb and min_size_mb > 0:
            base = base.where(
                ResourceVersions.file_size_bytes >= min_size_mb * 1024 * 1024
            )

        sort_field = sort_by if sort_by in self.VALID_SORT_FIELDS else "created_at"
        sort_attr = getattr(ResourceVersions, sort_field, ResourceVersions.created_at)
        order_col = sort_attr.desc() if sort_desc else sort_attr.asc()

        offset = (page - 1) * page_size
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(order_col).offset(offset).limit(page_size)
            )
            rows = [_version_row(o) for o in result.scalars().all()]
        return rows, (total or 0)

    async def resources_to_media(self, resource_ids: list[str]) -> dict[str, str]:
        """{str(resource_id): str(media_id)} for resources with a non-null media_id."""
        if not resource_ids:
            return {}
        stmt = select(Resources.id, Resources.media_id).where(
            Resources.id.in_([_bigint(r) for r in resource_ids])
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return {str(rid): str(mid) for rid, mid in result.all() if mid is not None}

    async def media_info_bulk(self, media_ids: list[str]) -> dict[str, dict[str, Any]]:
        """{str(media_id): {id, title, cover_urls, cover_download_path,
        source_platform, author}} for cover/title/author display."""
        if not media_ids:
            return {}
        stmt = select(
            ParsedMedia.id,
            ParsedMedia.title,
            ParsedMedia.cover_urls,
            ParsedMedia.cover_download_path,
            ParsedMedia.source_platform,
            ParsedMedia.author,
        ).where(ParsedMedia.id.in_([_bigint(m) for m in media_ids]))
        async with read_scope() as session:
            result = await session.execute(stmt)
            out: dict[str, dict[str, Any]] = {}
            for row in result.all():
                out[str(row.id)] = {
                    "id": row.id,
                    "title": row.title,
                    "cover_urls": row.cover_urls,
                    "cover_download_path": row.cover_download_path,
                    "source_platform": row.source_platform,
                    "author": row.author,
                }
        return out

    # ─── Mutations / single reads ──────────────────────────────────────

    async def get_version(self, version_id: str) -> Optional[dict[str, Any]]:
        """{id, resource_id, mime_type} for one version, or None (maybe_single
        parity — the legacy swallowed any error to None)."""
        stmt = (
            select(
                ResourceVersions.id,
                ResourceVersions.resource_id,
                ResourceVersions.mime_type,
            )
            .where(ResourceVersions.id == _bigint(version_id))
            .limit(1)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        return {
            "id": row.id,
            "resource_id": row.resource_id,
            "mime_type": row.mime_type,
        }

    async def mark_pending(self, version_id: str) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(ResourceVersions)
                .where(ResourceVersions.id == _bigint(version_id))
                .values(transcode_status="pending")
            )

    async def list_versions_for_batch(
        self, action: str, limit: int = BATCH_VERSIONS_LIMIT
    ) -> list[dict[str, Any]]:
        """retry_failed → failed videos; transcode_new → untranscoded (status NULL).
        Returns {id, resource_id, mime_type} rows (native int ids; router str()s).

        Bounded + ordered to match the REST twin: at most ``limit`` rows by id.
        The caller marks each ``pending`` so it leaves the set, making the batch
        re-runnable to drain past one call.
        """
        base = (
            select(
                ResourceVersions.id,
                ResourceVersions.resource_id,
                ResourceVersions.mime_type,
            )
            .where(ResourceVersions.mime_type.like("video/%"))
            .order_by(ResourceVersions.id.asc())
            .limit(limit)
        )
        if action == "retry_failed":
            base = base.where(ResourceVersions.transcode_status == "failed")
        else:  # transcode_new
            base = base.where(ResourceVersions.transcode_status.is_(None))

        async with read_scope() as session:
            result = await session.execute(base)
            return [
                {
                    "id": row.id,
                    "resource_id": row.resource_id,
                    "mime_type": row.mime_type,
                }
                for row in result.all()
            ]

    # ─── Settings ──────────────────────────────────────────────────────

    async def load_settings(self) -> dict[str, Any]:
        """{key: value} for keys LIKE 'transcode_%' (value is JSONB → native)."""
        stmt = select(SystemSettings.key, SystemSettings.value).where(
            SystemSettings.key.like("transcode_%")
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return {key: value for key, value in result.all()}

    async def upsert_setting(self, key: str, value: Any, updated_by: str) -> None:
        """Reproduce the supabase ``.upsert`` on the ``key`` PK: INSERT … ON CONFLICT
        (key) DO UPDATE SET value, updated_by. COMMITS via write_scope()."""
        stmt = pg_insert(SystemSettings).values(
            key=key, value=value, updated_by=updated_by
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SystemSettings.key],
            set_={"value": stmt.excluded.value, "updated_by": stmt.excluded.updated_by},
        )
        async with write_scope() as session:
            await session.execute(stmt)


__all__ = ["AdminTranscodeRepositoryOrm"]
