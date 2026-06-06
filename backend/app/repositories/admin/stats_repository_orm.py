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
  completed_videos_by_user → resource-centric (see RESOURCE-CENTRIC note below).

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
    The value now comes from ``resources.creator_id`` (the resource-centric fix).

RESOURCE-CENTRIC completed_videos_by_user (FIXED — schema-drift repair)
=======================================================================
``completed_videos_by_user`` formerly selected ``parsed_media.user_id`` — a
column DROPPED in migration 083 (``083_cleanup_parsed_media_user_fields.sql``;
parsed_media became a global content table, per-user ownership moved to
``resources``). That made the ``/storage`` admin endpoint 500 (PG 42703) under
both REST and ORM.

DECIDED FIX = resource-centric: count downloaded media per ``resources.creator_id``
where ``is_trashed`` is false. We ``select(Resources.creator_id)`` over the
``Resources`` model and emit one row per resource with a ``user_id`` key (aliased
from creator_id, coerced to str). This reproduces the EXACT row shape the
``/storage`` handler expects — it groups rows in Python by ``user_id`` into
total_videos_downloaded / unique_users / top_users — so the endpoint output shape
is unchanged. Both the legacy REST repo and this ORM repo apply the same fix.

Model-quirk scan: the only Enum touched is ParsedMedia.video_download_status
(handled via _plain). No renamed column is read here. No writes — READS ONLY.

DATE FILTER BINDING (v3 rule): every ``since`` filter binds a NATIVE tz-aware
datetime (naive → assume UTC), never an ISO string, so the timestamptz column
comparison does not raise the timestamptz<VARCHAR error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy import func, select

from app.db.session import read_scope
from app.models import ParsedMedia, Resources, Teams, UserLogs, UserProfiles
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
        """One row per non-trashed resource, keyed by owner (``creator_id`` → str
        aliased to ``user_id``).

        Resource-centric ownership (DECIDED FIX, see module docstring): the old
        ``parsed_media.user_id`` column was dropped in migration 083, so we count
        downloaded media per ``resources.creator_id`` where ``is_trashed`` is
        false. The ``/storage`` router groups these rows in Python by ``user_id``
        and counts, so we reproduce the SAME row shape it expects (one row per
        resource carrying a ``user_id`` key). creator_id (uuid) → str so it keys
        consistently in ``user_video_counts[user_id]`` (REST str shape)."""
        stmt = select(Resources.creator_id).where(Resources.is_trashed.is_(False))
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {"user_id": str(creator_id)}
                for (creator_id,) in result.all()
                if creator_id is not None
            ]


__all__ = ["AdminStatsRepositoryOrm"]
