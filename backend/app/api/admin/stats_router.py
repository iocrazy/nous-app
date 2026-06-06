"""Admin statistics API routes."""

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Query

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


@router.get("/users/growth")
async def get_user_growth(
    auth: AdminAuthDep,
    days: int = Query(30, ge=7, le=365),
):
    """Get user registration growth over time."""
    repo = get_admin_stats_repository()
    start_date = datetime.utcnow() - timedelta(days=days)
    rows = await repo.user_registrations_since(start_date)

    daily_counts: dict[str, int] = {}
    for user in rows:
        day = user["created_at"][:10]
        daily_counts[day] = daily_counts.get(day, 0) + 1

    data = []
    current = start_date.date()
    end = datetime.utcnow().date()
    while current <= end:
        day_str = current.isoformat()
        data.append({"date": day_str, "count": daily_counts.get(day_str, 0)})
        current += timedelta(days=1)

    return {"data": data}


@router.get("/videos/stats")
async def get_video_stats(
    auth: AdminAuthDep,
    days: int = Query(30, ge=7, le=365),
):
    """Get video statistics over time."""
    repo = get_admin_stats_repository()
    start_date = datetime.utcnow() - timedelta(days=days)
    rows = await repo.video_status_history(start_date)

    daily_data: dict[str, dict[str, int]] = {}
    for video in rows:
        day = video["created_at"][:10]
        status = video["video_download_status"]

        if day not in daily_data:
            daily_data[day] = {"total": 0, "completed": 0, "failed": 0}

        daily_data[day]["total"] += 1
        if status == "completed":
            daily_data[day]["completed"] += 1
        elif status == "failed":
            daily_data[day]["failed"] += 1

    data = []
    current = start_date.date()
    end = datetime.utcnow().date()
    while current <= end:
        day_str = current.isoformat()
        day_data = daily_data.get(day_str, {"total": 0, "completed": 0, "failed": 0})
        data.append({"date": day_str, **day_data})
        current += timedelta(days=1)

    return {"data": data}


@router.get("/storage")
async def get_storage_stats(auth: AdminAuthDep):
    """Get storage usage statistics."""
    repo = get_admin_stats_repository()
    rows = await repo.completed_videos_by_user()

    user_video_counts: dict[str, int] = {}
    for video in rows:
        user_id = video["user_id"]
        user_video_counts[user_id] = user_video_counts.get(user_id, 0) + 1

    top_users = sorted(user_video_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_videos_downloaded": len(rows),
        "unique_users": len(user_video_counts),
        "top_users": [{"user_id": u[0], "video_count": u[1]} for u in top_users],
    }
