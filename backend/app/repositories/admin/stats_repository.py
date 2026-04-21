"""Repository for admin dashboard stats — aggregates reads across tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_async_supabase_admin


class AdminStatsRepository:
    async def _client(self):
        return await get_async_supabase_admin()

    async def count_user_profiles(self, since: datetime | None = None) -> int:
        client = await self._client()
        query = client.table("user_profiles").select("id", count="exact")
        if since is not None:
            query = query.gte("created_at", since.isoformat())
        result = await query.execute()
        return result.count or 0

    async def count_parsed_media(
        self,
        *,
        since: datetime | None = None,
        video_download_status: str | None = None,
    ) -> int:
        client = await self._client()
        query = client.table("parsed_media").select("id", count="exact")
        if video_download_status is not None:
            query = query.eq("video_download_status", video_download_status)
        if since is not None:
            query = query.gte("created_at", since.isoformat())
        result = await query.execute()
        return result.count or 0

    async def count_teams(self) -> int:
        client = await self._client()
        result = await client.table("teams").select("id", count="exact").execute()
        return result.count or 0

    async def distinct_active_users_since(self, since: datetime) -> int:
        """Count distinct user_ids in user_logs since `since`."""
        client = await self._client()
        try:
            result = (
                await client.table("user_logs")
                .select("user_id")
                .gte("created_at", since.isoformat())
                .execute()
            )
            return len({log["user_id"] for log in result.data}) if result.data else 0
        except Exception:
            return 0

    async def user_registrations_since(self, since: datetime) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table("user_profiles")
            .select("created_at")
            .gte("created_at", since.isoformat())
            .execute()
        )
        return result.data or []

    async def video_status_history(self, since: datetime) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table("parsed_media")
            .select("created_at, video_download_status")
            .gte("created_at", since.isoformat())
            .execute()
        )
        return result.data or []

    async def completed_videos_by_user(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table("parsed_media")
            .select("user_id, video_download_status")
            .eq("video_download_status", "completed")
            .execute()
        )
        return result.data or []
