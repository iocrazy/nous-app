"""Admin statistics API routes."""

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.schemas.admin import AdminStatsResponse

router = APIRouter()


@router.get("/overview", response_model=AdminStatsResponse)
async def get_overview_stats(auth: AdminAuthDep):
    """Get dashboard overview statistics."""
    supabase = await get_async_supabase_admin()

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())

    async def get_active_users() -> int:
        """Get active users today from user_logs."""
        try:
            result = await supabase.table("user_logs").select("user_id").gte("created_at", today_start.isoformat()).execute()
            return len(set(log["user_id"] for log in result.data)) if result.data else 0
        except Exception:
            return 0

    # Execute all queries concurrently
    (
        users_result,
        videos_result,
        teams_result,
        downloads_result,
        new_users_result,
        new_videos_result,
        active_users_today,
    ) = await asyncio.gather(
        supabase.table("user_profiles").select("id", count="exact").execute(),
        supabase.table("douyin_videos").select("id", count="exact").execute(),
        supabase.table("teams").select("id", count="exact").execute(),
        supabase.table("douyin_videos").select("id", count="exact").eq("video_download_status", "completed").execute(),
        supabase.table("user_profiles").select("id", count="exact").gte("created_at", today_start.isoformat()).execute(),
        supabase.table("douyin_videos").select("id", count="exact").gte("created_at", today_start.isoformat()).execute(),
        get_active_users(),
    )

    return AdminStatsResponse(
        total_users=users_result.count or 0,
        total_videos=videos_result.count or 0,
        total_teams=teams_result.count or 0,
        total_downloads=downloads_result.count or 0,
        active_users_today=active_users_today,
        new_users_today=new_users_result.count or 0,
        new_videos_today=new_videos_result.count or 0,
    )


@router.get("/users/growth")
async def get_user_growth(
    auth: AdminAuthDep,
    days: int = Query(30, ge=7, le=365),
):
    """Get user registration growth over time."""
    supabase = await get_async_supabase_admin()
    start_date = datetime.utcnow() - timedelta(days=days)

    result = await supabase.table("user_profiles").select("created_at").gte("created_at", start_date.isoformat()).execute()

    daily_counts: dict[str, int] = {}
    for user in result.data:
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
    supabase = await get_async_supabase_admin()
    start_date = datetime.utcnow() - timedelta(days=days)

    result = await supabase.table("douyin_videos").select("created_at, video_download_status").gte("created_at", start_date.isoformat()).execute()

    daily_data: dict[str, dict[str, int]] = {}
    for video in result.data:
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
    supabase = await get_async_supabase_admin()

    result = await supabase.table("douyin_videos").select("user_id, video_download_status").eq("video_download_status", "completed").execute()

    user_video_counts: dict[str, int] = {}
    for video in result.data:
        user_id = video["user_id"]
        user_video_counts[user_id] = user_video_counts.get(user_id, 0) + 1

    top_users = sorted(user_video_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_videos_downloaded": len(result.data),
        "unique_users": len(user_video_counts),
        "top_users": [{"user_id": u[0], "video_count": u[1]} for u in top_users],
    }
