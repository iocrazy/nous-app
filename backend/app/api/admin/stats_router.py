"""Admin statistics API routes."""

import asyncio
from datetime import datetime

from fastapi import APIRouter

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.stats_repository import get_admin_stats_repository
from app.schemas.admin import AdminStatsResponse

router = APIRouter()


@router.get("/overview", response_model=AdminStatsResponse)
async def get_overview_stats(auth: AdminAuthDep):
    """Get dashboard overview statistics."""
    repo = get_admin_stats_repository()

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())

    (
        total_users,
        total_videos,
        total_teams,
        total_downloads,
        new_users_today,
        new_videos_today,
        active_users_today,
    ) = await asyncio.gather(
        repo.count_user_profiles(),
        repo.count_parsed_media(),
        repo.count_teams(),
        repo.count_parsed_media(video_download_status="completed"),
        repo.count_user_profiles(since=today_start),
        repo.count_parsed_media(since=today_start),
        repo.distinct_active_users_since(today_start),
    )

    return AdminStatsResponse(
        total_users=total_users,
        total_videos=total_videos,
        total_teams=total_teams,
        total_downloads=total_downloads,
        active_users_today=active_users_today,
        new_users_today=new_users_today,
        new_videos_today=new_videos_today,
    )
