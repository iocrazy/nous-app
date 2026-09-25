"""Admin stats repository — data access for the admin dashboard aggregations.

ORM 2.0 (post-rollout cleanup): ``AdminStatsRepository`` is the SQLAlchemy 2.0
implementation of the admin dashboard aggregations over ``user_profiles`` /
``parsed_media`` / ``teams`` / ``user_logs``. The legacy
supabase-py REST path and its per-domain rollout flag have been retired; call
sites go through ``get_admin_stats_repository()`` (bottom of this file) which
now unconditionally returns this repository.

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

The per-day growth / video-status / per-user storage aggregations
(``user_registrations_since`` / ``video_status_history`` /
``completed_videos_by_user``) were deleted with the three ``/admin/stats``
routes they served (users/growth, videos/stats, storage), which no client
ever called (P8, 2026-09-24).

DATE FILTER BINDING (v3 rule): every ``since`` filter binds a NATIVE tz-aware
datetime (naive → assume UTC), never an ISO string, so the timestamptz column
comparison does not raise the timestamptz<VARCHAR error.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.session import read_scope
from app.models import ParsedMedia, Teams, UserLogs, UserProfiles


def _aware(dt: datetime) -> datetime:
    """tz-aware datetime for a timestamptz filter bind (naive → assume UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class AdminStatsRepository:
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


def get_admin_stats_repository() -> "AdminStatsRepository":
    """Return the AdminStatsRepository (SQLAlchemy 2.0 ORM).

    The per-domain rollout flag and the legacy supabase-py REST path have been
    retired post-rollout; this now unconditionally returns the ORM
    implementation."""
    return AdminStatsRepository()
