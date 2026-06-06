"""SQLAlchemy 2.0 ORM implementation of AdminStatsRepository (Phase 2 admin wave).

REST → ORM successor for the admin dashboard aggregations across
``user_profiles`` / ``parsed_media`` / ``teams`` / ``user_logs``.
``AdminStatsRepositoryOrm`` subclasses ``AdminStatsRepository`` and overrides
every method. Call sites route through ``get_admin_stats_repository()`` (bottom
of ``stats_repository.py``).

MODELS (all reflected, verified): UserProfiles (user_profiles), ParsedMedia
(parsed_media), Teams (teams), UserLogs (user_logs).

AGGREGATION REPRODUCTION
========================
  count_user_profiles / count_parsed_media / count_teams → exact COUNT(*) via
    ``select(func.count()).select_from(Model)`` + the SAME equality/since filters.
    COUNT returns a NATIVE int (the 5.3 trap — AdminStatsResponse fields are int).
  distinct_active_users_since → the legacy fetched every ``user_id`` from
    user_logs since the cutoff and counted the distinct set in Python (wrapped in
    a try/except → 0 on error). We push the DISTINCT to PG:
    ``COUNT(DISTINCT user_id)`` (equivalent result, returns native int) and KEEP
    the legacy's swallow-to-0 on any error (parity — the legacy never propagated).
  user_registrations_since → rows of {created_at} since the cutoff.
  video_status_history → rows of {created_at, video_download_status} since cutoff.
  completed_videos_by_user → see BROKEN-ENDPOINT note below.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at (timestamptz) → **.isoformat()** ALWAYS. CONSUMED: both the
    user-growth and video-stats endpoints do ``row["created_at"][:10]`` (string
    slicing to bucket by day) — a native datetime is not subscriptable.
  video_download_status → ParsedMedia maps this column as a SQLAlchemy
    ``Enum(DownloadStatus)``, so an ORM read returns an Enum MEMBER, whereas REST
    returned the bare string. CONSUMED: the router does ``status == "completed"``
    / ``status == "failed"``. Enum members ARE str subclasses so ``==`` happens to
    work, but for byte-exact REST parity we unwrap to the bare ``.value`` via
    ``_plain`` (e.g. "completed" not DownloadStatus.COMPLETED).
  user_id (uuid) → **str** in completed_videos_by_user — see below: it is a DICT
    KEY in the storage endpoint (``user_video_counts[user_id]``) and returned in
    the response, so a native uuid would key inconsistently vs the REST str shape.

★ BROKEN-ENDPOINT (DO-NOT-REPAIR — inert discipline) ★
======================================================
``completed_videos_by_user`` selects ``parsed_media.user_id`` — a column that was
**DROPPED in migration 083** (``083_cleanup_parsed_media_user_fields.sql``;
parsed_media became a global content table, per-user ownership moved to
``resources``). Verified live: ``information_schema`` reports 0 such column.

Under REST this query already FAILS: PostgREST returns PG error 42703 ("column
parsed_media.user_id does not exist"), so the ``/storage`` admin endpoint 500s
today. Per the migration's inert discipline, the ORM MUST NOT silently REPAIR a
broken prod endpoint. We therefore reproduce the SAME failure faithfully: a raw
``text("SELECT user_id, video_download_status FROM parsed_media ...")`` inside the
read scope raises ``UndefinedColumnError`` (the asyncpg flavour of 42703) — the
same class of error REST surfaces. We deliberately DO NOT route this through the
``ParsedMedia`` model (which has no ``user_id`` attribute) and DO NOT substitute a
working column. This is flagged as a CONCERN for follow-up, not fixed here.

Model-quirk scan: the only Enum touched is ParsedMedia.video_download_status
(handled via _plain). No renamed column is read here. No writes — READS ONLY.

DATE FILTER BINDING (v3 rule): every ``since`` filter binds a NATIVE tz-aware
datetime (naive → assume UTC), never an ISO string, so the timestamptz column
comparison does not raise the timestamptz<VARCHAR error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy import func, select, text

from app.db.session import read_scope
from app.models import ParsedMedia, Teams, UserLogs, UserProfiles
from app.repositories._orm_helpers import _plain
from app.repositories.admin.stats_repository import AdminStatsRepository


def _aware(dt: datetime) -> datetime:
    """tz-aware datetime for a timestamptz filter bind (naive → assume UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Any) -> Any:
    """ISO-string a datetime (NULL/non-datetime pass through)."""
    return dt.isoformat() if isinstance(dt, datetime) else dt


class AdminStatsRepositoryOrm(AdminStatsRepository):
    """ORM-backed AdminStatsRepository (admin dashboard aggregations)."""

    async def count_user_profiles(self, since: datetime | None = None) -> int:
        stmt = select(func.count()).select_from(UserProfiles)
        if since is not None:
            stmt = stmt.where(UserProfiles.created_at >= _aware(since))
        async with read_scope() as session:
            return (await session.scalar(stmt)) or 0

    async def count_parsed_media(
        self,
        *,
        since: datetime | None = None,
        video_download_status: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(ParsedMedia)
        if video_download_status is not None:
            stmt = stmt.where(
                ParsedMedia.video_download_status == video_download_status
            )
        if since is not None:
            stmt = stmt.where(ParsedMedia.created_at >= _aware(since))
        async with read_scope() as session:
            return (await session.scalar(stmt)) or 0

    async def count_teams(self) -> int:
        async with read_scope() as session:
            return (await session.scalar(select(func.count()).select_from(Teams))) or 0

    async def distinct_active_users_since(self, since: datetime) -> int:
        """Count distinct user_ids in user_logs since ``since``. Pushes DISTINCT
        to PG (COUNT(DISTINCT user_id)); preserves the legacy swallow-to-0 on any
        error (the legacy wrapped the whole call in try/except → 0)."""
        try:
            stmt = select(func.count(func.distinct(UserLogs.user_id))).where(
                UserLogs.created_at >= _aware(since)
            )
            async with read_scope() as session:
                return (await session.scalar(stmt)) or 0
        except Exception:
            return 0

    async def user_registrations_since(self, since: datetime) -> List[dict[str, Any]]:
        """{created_at} rows since ``since`` (created_at → ISO str, consumed by
        the growth endpoint's created_at[:10] slicing)."""
        stmt = select(UserProfiles.created_at).where(
            UserProfiles.created_at >= _aware(since)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [{"created_at": _iso(created_at)} for (created_at,) in result.all()]

    async def video_status_history(self, since: datetime) -> List[dict[str, Any]]:
        """{created_at, video_download_status} rows since ``since``. created_at →
        ISO str; video_download_status Enum → bare .value (consumed by the
        router's status == 'completed' / 'failed' compares)."""
        stmt = select(ParsedMedia.created_at, ParsedMedia.video_download_status).where(
            ParsedMedia.created_at >= _aware(since)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "created_at": _iso(created_at),
                    "video_download_status": _plain(video_download_status),
                }
                for created_at, video_download_status in result.all()
            ]

    async def completed_videos_by_user(self) -> List[dict[str, Any]]:
        """BROKEN ENDPOINT — reproduced faithfully, NOT repaired.

        Selects ``parsed_media.user_id``, dropped in migration 083 (verified
        absent live). REST returns PG 42703 here; we reproduce the SAME failure
        via a raw text() select (raises UndefinedColumnError, the asyncpg 42703).
        We deliberately do NOT route this through the ParsedMedia model (no
        ``user_id`` attr) and do NOT substitute a working column — inert
        discipline forbids repairing a broken prod endpoint. See the module
        docstring's BROKEN-ENDPOINT note. user_id (uuid) → str would apply IF the
        column existed (it is a dict key in the /storage router)."""
        stmt = text(
            "SELECT user_id, video_download_status FROM parsed_media "
            "WHERE video_download_status = 'completed'"
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            rows = result.mappings().all()
        return [
            {
                "user_id": str(r["user_id"]) if r["user_id"] is not None else None,
                "video_download_status": _plain(r["video_download_status"]),
            }
            for r in rows
        ]


__all__ = ["AdminStatsRepositoryOrm"]
