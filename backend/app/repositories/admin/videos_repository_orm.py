"""SQLAlchemy 2.0 ORM implementation of AdminVideosRepository (Phase 2 admin wave).

REST → ORM successor for the admin video-management console, which reads (and, for
delete / retry-reset, WRITES) the ``parsed_media`` table. ``AdminVideosRepositoryOrm``
subclasses ``AdminVideosRepository`` and overrides every data method; the ``TABLE``
/ ``ALLOWED_SORT_FIELDS`` constants are inherited. Call sites route through
``get_admin_videos_repository()`` (bottom of ``videos_repository.py``).

MODEL: ``app.models.ParsedMedia`` (table ``parsed_media``) — verified reflected.
(The repo / router say "videos" but the table is ``parsed_media``; the ``videos``
table was renamed to ``parsed_media`` — there is no separate ``videos`` model.)

★ parsed_media.user_id NOTE (mig 083) — NOT touched here ★
==========================================================
``parsed_media.user_id`` was DROPPED in migration 083 (verified live:
information_schema reports no such column). This admin repo NEVER selects or
writes ``user_id`` (the per-user-storage query that does live in the *stats* repo,
which preserves its own broken endpoint). SELECT * here returns whatever columns
exist on the live table; there is no reference to the dropped column, so no broken
endpoint exists in THIS repo. (Flagged only so a reader doesn't re-introduce it.)

★ UUID AUDIT (admin reads-across-all-users; service_role scope) ★
=================================================================
``parsed_media`` has NO uuid column in its current schema (``id`` is BIGINT
Snowflake; ``user_id`` was dropped). SELECT * therefore returns no uuid value →
NO uuid coercion is needed / no ``==`` / dict-key / ``UUID()`` consumer exists.
``id`` (BigInteger) → native int (``AdminVideoResponse.id: int``). The defensive
uuid→str sweep in ``_row`` is kept for safety but is a no-op on this table.

ENUM COLUMNS (the parity trap)
==============================
``video_download_status`` / ``music_download_status`` / ``cover_download_status``
are mapped as SQLAlchemy ``Enum(DownloadStatus)`` → an ORM read returns an Enum
MEMBER, whereas REST returned the bare string. CONSUMED: the router sets them on
``AdminVideo[Detail]Response.*_download_status: str`` and the stats endpoints
compare ``status == "completed"`` / "failed". We unwrap every Enum to its bare
``.value`` via ``_orm_obj_to_dict`` (which applies ``_plain``), so e.g.
"completed" not ``DownloadStatus.COMPLETED`` — byte-exact REST parity.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at / download_time / updated_at / published_at / etc. (timestamptz) →
    **.isoformat()** ALWAYS. CONSUMED: ``AdminVideoResponse.created_at: datetime``
    / ``download_time: Optional[datetime]`` / ``updated_at: Optional[datetime]``
    Pydantic fields parse the ISO str fine; ``created_at`` is read as ``v["created_at"]``
    (must be present — SELECT * provides it).
  id / datasize_bytes / like_count / comment_count / share_count / favorite_count
    / view_count / storage_size (int/bigint) → native int.
  count_total / count_by_status → exact COUNT(*) → native int (the 5.3 trap;
    AdminVideoStatsResponse fields are int).
  sum_storage_bytes → SUM pushed to PG (the legacy pulled all sizes and summed in
    Python; same result), returned as a native int (COALESCE to 0).
  cover_urls / video_download_urls / image_download_urls (jsonb) → native list.
  title / description / author / platform_id / source_platform / error_message /
    download_path / etc. (text/varchar) → native str.

LIST FILTERS / SEARCH (reproduced exactly)
------------------------------------------
  search → ``title ILIKE %s% OR platform_id ILIKE %s%`` (via ``or_``).
  video_download_status → eq (bind the bare string; the Enum column accepts its
    value). source_platform → eq.
  sort_by validated against ALLOWED_SORT_FIELDS (else "created_at"), desc/asc.
  Paginated via offset/limit. NO date-range filter exists in this repo.

WRITE PATHS (the silent-rollback P0 lesson) — ALL commit via write_scope()
--------------------------------------------------------------------------
  delete(video_id) → DELETE ... RETURNING id; returns bool(rowcount) — REST
    returned ``bool(result.data)``.
  reset_for_retry(video_id) → UPDATE video_download_status='pending',
    error_message=NULL ... RETURNING id; returns bool. Both bind only REAL mapped
    columns (no phantom). reset_for_retry writes business columns (not a
    DBOS-mirrored table — task_tracking discipline does not apply to parsed_media).
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import ParsedMedia
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.videos_repository import AdminVideosRepository

_PM_N2A: Dict[str, str] = _name_to_attr(ParsedMedia)


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict: Enum → bare .value (via
    _orm_obj_to_dict/_plain), datetime → ISO str. uuid→str sweep kept defensively
    (no-op on parsed_media). NULLs pass through."""
    out = _orm_obj_to_dict(obj, _PM_N2A)
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class AdminVideosRepositoryOrm(AdminVideosRepository):
    """ORM-backed AdminVideosRepository (admin parsed_media reads + delete/reset)."""

    # ─── Stats ─────────────────────────────────────────────────────────

    async def count_total(self) -> int:
        async with read_scope() as session:
            total = await session.scalar(select(func.count()).select_from(ParsedMedia))
        return total or 0

    async def count_by_status(self, status: str) -> int:
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ParsedMedia)
                .where(ParsedMedia.video_download_status == status)
            )
        return total or 0

    async def sum_storage_bytes(self) -> int:
        """Total bytes across media with datasize_bytes > 0. The legacy pulled all
        sizes and summed in Python; we push SUM to PG (same result) → native int."""
        stmt = select(func.coalesce(func.sum(ParsedMedia.datasize_bytes), 0)).where(
            ParsedMedia.datasize_bytes > 0
        )
        async with read_scope() as session:
            total = await session.scalar(stmt)
        return int(total or 0)

    async def counts_by_statuses(self, statuses: list[str]) -> dict[str, int]:
        """Parallel fanout — one COUNT query per status, all in flight at once
        (reproduces the legacy asyncio.gather)."""
        results = await asyncio.gather(*[self.count_by_status(s) for s in statuses])
        return dict(zip(statuses, results))

    # ─── List / Get ────────────────────────────────────────────────────

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        video_download_status: Optional[str] = None,
        source_platform: Optional[str] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(ParsedMedia)
        if search:
            pat = f"%{search}%"
            base = base.where(
                or_(
                    ParsedMedia.title.ilike(pat),
                    ParsedMedia.platform_id.ilike(pat),
                )
            )
        if video_download_status:
            base = base.where(
                ParsedMedia.video_download_status == video_download_status
            )
        if source_platform:
            base = base.where(ParsedMedia.source_platform == source_platform)

        sort_field = sort_by if sort_by in self.ALLOWED_SORT_FIELDS else "created_at"
        sort_attr = getattr(ParsedMedia, sort_field, ParsedMedia.created_at)
        order_col = sort_attr.desc() if sort_desc else sort_attr.asc()

        offset = (page - 1) * page_size
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(order_col).offset(offset).limit(page_size)
            )
            rows = [_row(o) for o in result.scalars().all()]
        return rows, (total or 0)

    async def get_by_id(self, video_id: int) -> Optional[dict[str, Any]]:
        """SELECT * for one parsed_media row by id, or None. The legacy swallowed
        any error to None (maybe_single); we reproduce the None-on-absent contract
        (a genuine query error still raises, matching maybe_single semantics for a
        present-but-unreadable row is N/A here)."""
        stmt = select(ParsedMedia).where(ParsedMedia.id == video_id).limit(1)
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _row(obj) if obj is not None else None

    # ─── Mutations ─────────────────────────────────────────────────────

    async def delete(self, video_id: int) -> bool:
        """DELETE by id; COMMITS via write_scope(). Returns True iff a row was
        deleted (REST returned bool(result.data))."""
        stmt = (
            sa_delete(ParsedMedia)
            .where(ParsedMedia.id == video_id)
            .returning(ParsedMedia.id)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            deleted = result.first() is not None
        return deleted

    async def reset_for_retry(self, video_id: int) -> bool:
        """Reset a row to retryable state (video_download_status='pending',
        error_message=NULL); COMMITS via write_scope(). Returns True iff a row was
        updated (REST returned bool(result.data))."""
        stmt = (
            sa_update(ParsedMedia)
            .where(ParsedMedia.id == video_id)
            .values(video_download_status="pending", error_message=None)
            .returning(ParsedMedia.id)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            updated = result.first() is not None
        return updated


__all__ = ["AdminVideosRepositoryOrm"]
