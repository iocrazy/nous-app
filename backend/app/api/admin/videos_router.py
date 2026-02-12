"""Admin API routes for Video management."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.schemas.admin import (
    AdminVideoResponse,
    AdminVideoListResponse,
    AdminVideoDetailResponse,
    AdminVideoStatsResponse,
)
from app.utils.admin_helpers import (
    batch_get_user_emails,
    create_audit_log,
)


router = APIRouter()


# ============================================
# Video Endpoints
# ============================================


@router.get("/stats", response_model=AdminVideoStatsResponse)
async def get_video_stats(auth: AdminAuthDep):
    """Get video status distribution statistics."""
    supabase = await get_async_supabase_admin()

    # Get total count
    total_result = await supabase.table("douyin_videos").select("id", count="exact").execute()
    total = total_result.count or 0

    # Get counts by status
    statuses = ["completed", "pending", "failed", "downloading", "skipped"]
    counts = {}
    for s in statuses:
        result = await supabase.table("douyin_videos").select(
            "id", count="exact"
        ).eq("video_download_status", s).execute()
        counts[s] = result.count or 0

    # Get total storage bytes
    storage_result = await supabase.table("douyin_videos").select(
        "video_datasize_bytes"
    ).gt("video_datasize_bytes", 0).execute()

    total_storage = sum(
        (row.get("video_datasize_bytes") or 0)
        for row in (storage_result.data or [])
    )

    return AdminVideoStatsResponse(
        total=total,
        completed=counts.get("completed", 0),
        pending=counts.get("pending", 0),
        failed=counts.get("failed", 0),
        downloading=counts.get("downloading", 0),
        skipped=counts.get("skipped", 0),
        total_storage_bytes=total_storage,
    )


@router.get("", response_model=AdminVideoListResponse)
async def list_videos(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by title or aweme_id"),
    video_download_status: Optional[str] = Query(None, alias="status", description="Filter by download status"),
    aweme_type: Optional[str] = Query(None, description="Filter by aweme type"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order (asc/desc)"),
):
    """
    List all videos with pagination and filters.

    - **page**: Page number (starts at 1)
    - **page_size**: Number of items per page (max 100)
    - **search**: Search by video title or aweme_id
    - **status**: Filter by download status
    - **aweme_type**: Filter by aweme type (0, 2, 4, 61, 68)
    - **user_id**: Filter by user ID
    - **sort_by**: Sort by field (created_at, video_datasize_bytes, video_download_status)
    - **sort_order**: Sort order (asc, desc)
    """
    supabase = await get_async_supabase_admin()

    # Build query
    query = supabase.table("douyin_videos").select("*", count="exact")

    # Apply filters
    if search:
        query = query.or_(f"video_title.ilike.%{search}%,aweme_id.ilike.%{search}%")

    if video_download_status:
        query = query.eq("video_download_status", video_download_status)

    if aweme_type:
        query = query.eq("aweme_type", aweme_type)

    if user_id:
        query = query.eq("user_id", user_id)

    # Apply sorting
    allowed_sort_fields = {"created_at", "video_datasize_bytes", "video_download_status"}
    if sort_by not in allowed_sort_fields:
        sort_by = "created_at"
    desc = sort_order.lower() != "asc"
    query = query.order(sort_by, desc=desc)

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    # Execute query
    result = await query.execute()

    if not result.data:
        return AdminVideoListResponse(items=[], total=0, page=page, page_size=page_size)

    # Batch fetch user emails (avoiding N+1)
    user_ids = list({v["user_id"] for v in result.data if v.get("user_id")})
    user_emails = await batch_get_user_emails(user_ids)

    # Build response
    items = []
    for v in result.data:
        items.append(AdminVideoResponse(
            id=v["id"],
            aweme_id=str(v.get("aweme_id", "")),
            user_id=v.get("user_id"),
            user_email=user_emails.get(v.get("user_id", "")) if v.get("user_id") else None,
            video_title=v.get("video_title"),
            video_desc=v.get("video_desc"),
            author=v.get("author"),
            aweme_type=str(v.get("aweme_type")) if v.get("aweme_type") is not None else None,
            video_download_status=v.get("video_download_status", "pending"),
            cover_url=v.get("cover_url"),
            video_duration=str(v.get("video_duration")) if v.get("video_duration") is not None else None,
            video_datasize=v.get("video_datasize"),
            video_datasize_bytes=v.get("video_datasize_bytes") or 0,
            video_digg_count=v.get("video_digg_count") or 0,
            video_comment_count=v.get("video_comment_count") or 0,
            video_share_count=v.get("video_share_count") or 0,
            error_message=v.get("error_message"),
            download_time=v.get("download_time"),
            created_at=v["created_at"],
            updated_at=v.get("updated_at"),
        ))

    return AdminVideoListResponse(
        items=items,
        total=result.count or len(items),
        page=page,
        page_size=page_size,
    )


@router.get("/{video_id}", response_model=AdminVideoDetailResponse)
async def get_video(
    video_id: int,
    auth: AdminAuthDep,
):
    """Get detailed information about a specific video."""
    supabase = await get_async_supabase_admin()

    result = await supabase.table("douyin_videos").select("*").eq("id", video_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found",
        )

    v = result.data

    # Get user email if user_id exists
    user_email = None
    if v.get("user_id"):
        emails = await batch_get_user_emails([v["user_id"]])
        user_email = emails.get(v["user_id"])

    return AdminVideoDetailResponse(
        id=v["id"],
        aweme_id=str(v.get("aweme_id", "")),
        user_id=v.get("user_id"),
        user_email=user_email,
        video_title=v.get("video_title"),
        video_desc=v.get("video_desc"),
        author=v.get("author"),
        aweme_type=str(v.get("aweme_type")) if v.get("aweme_type") is not None else None,
        video_download_status=v.get("video_download_status", "pending"),
        cover_url=v.get("cover_url"),
        video_duration=str(v.get("video_duration")) if v.get("video_duration") is not None else None,
        video_datasize=v.get("video_datasize"),
        video_datasize_bytes=v.get("video_datasize_bytes") or 0,
        video_digg_count=v.get("video_digg_count") or 0,
        video_comment_count=v.get("video_comment_count") or 0,
        video_share_count=v.get("video_share_count") or 0,
        error_message=v.get("error_message"),
        download_time=v.get("download_time"),
        created_at=v["created_at"],
        updated_at=v.get("updated_at"),
        video_original_url=v.get("video_original_url"),
        video_download_path=v.get("video_download_path"),
        cover_download_path=v.get("cover_download_path"),
        music_name=v.get("music_name"),
        music_download_status=v.get("music_download_status", "pending"),
        cover_download_status=v.get("cover_download_status", "pending"),
        video_download_urls=v.get("video_download_urls"),
        video_hashtag_name=v.get("video_hashtag_name"),
        video_collect_count=v.get("video_collect_count") or 0,
        view_count=v.get("view_count") or 0,
        storage_size=v.get("storage_size"),
        keep_forever=v.get("keep_forever", False),
    )


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(
    video_id: int,
    auth: AdminAuthDep,
    request: Request,
):
    """Delete a video by its database ID."""
    supabase = await get_async_supabase_admin()

    # Check if video exists
    existing = await supabase.table("douyin_videos").select("id, aweme_id").eq("id", video_id).single().execute()
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found",
        )

    aweme_id = existing.data.get("aweme_id", "")

    # Delete video
    result = await supabase.table("douyin_videos").delete().eq("id", video_id).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete video",
        )

    # Audit log
    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="delete_video",
        target_type="video",
        target_id=str(video_id),
        details={"aweme_id": aweme_id},
        ip_address=client_ip,
    )

    logger.info(f"Video {video_id} (aweme_id={aweme_id}) deleted by admin {auth.user_id}")


@router.post("/{video_id}/retry", response_model=AdminVideoDetailResponse)
async def retry_video(
    video_id: int,
    auth: AdminAuthDep,
    request: Request,
):
    """Retry a failed video download by resetting its status to pending."""
    supabase = await get_async_supabase_admin()

    # Check if video exists
    existing = await supabase.table("douyin_videos").select("*").eq("id", video_id).single().execute()
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found",
        )

    # Reset download status
    result = await supabase.table("douyin_videos").update({
        "video_download_status": "pending",
        "error_message": None,
    }).eq("id", video_id).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retry video",
        )

    # Audit log
    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="retry_video",
        target_type="video",
        target_id=str(video_id),
        details={"aweme_id": existing.data.get("aweme_id", "")},
        ip_address=client_ip,
    )

    logger.info(f"Video {video_id} retry triggered by admin {auth.user_id}")

    # Return updated video
    return await get_video(video_id, auth)
