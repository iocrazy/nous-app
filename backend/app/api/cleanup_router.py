"""API routes for Cleanup Suggestions."""
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.services.cleanup_service import CleanupService
from app.schemas.cleanup import (
    CleanupSuggestion as CleanupSuggestionSchema,
    CleanupSuggestionsResponse,
    CleanupAction,
    CleanupBatchAction,
    CleanupStats,
)


router = APIRouter(prefix="/cleanup", tags=["Cleanup"])


@router.get("/suggestions", response_model=CleanupSuggestionsResponse)
async def get_cleanup_suggestions(
    limit: int = Query(50, ge=1, le=200),
    include_duplicates: bool = Query(True, description="Include potential duplicate detection"),
    auth: AuthDep = None,
):
    """
    Get cleanup suggestions for the current user.

    Analyzes your video library and suggests videos that could be deleted:
    - Videos never viewed (downloaded > 7 days ago)
    - Videos not viewed in 30+ days
    - Large files taking up space
    - Potential duplicates (similar content)
    """
    service = CleanupService()

    suggestions, categories = await service.get_suggestions(
        user_id=auth.user_id,
        limit=limit,
        include_duplicates=include_duplicates
    )

    total_reclaimable = sum(s.storage_size for s in suggestions)

    return CleanupSuggestionsResponse(
        suggestions=[
            CleanupSuggestionSchema(
                video_id=s.video_id,
                title=s.title,
                cover_url=s.cover_url,
                author=s.author,
                reason=s.reason,
                reason_detail=s.reason_detail,
                storage_size=s.storage_size,
                created_at=s.created_at,
                last_viewed_at=s.last_viewed_at,
                view_count=s.view_count,
                similarity_to=s.similarity_to,
                similarity_score=s.similarity_score
            )
            for s in suggestions
        ],
        total_count=len(suggestions),
        total_reclaimable_bytes=total_reclaimable,
        categories=categories
    )


@router.get("/stats", response_model=CleanupStats)
async def get_cleanup_stats(auth: AuthDep = None):
    """
    Get overall cleanup statistics.

    Provides an overview of your library's storage usage and cleanup potential.
    """
    service = CleanupService()
    stats = await service.get_cleanup_stats(auth.user_id)

    return CleanupStats(**stats)


@router.post("/videos/{video_id}/action")
async def take_cleanup_action(
    video_id: int,
    action: CleanupAction,
    auth: AuthDep = None,
):
    """
    Take action on a cleanup suggestion.

    Actions:
    - **keep_forever**: Mark video to never be suggested for cleanup
    - **dismiss**: Ignore this suggestion (temporary)
    - **delete**: Delete the video (moves to trash or permanent delete)
    """
    service = CleanupService()

    if action.action == "keep_forever":
        success = await service.mark_keep_forever(video_id, auth.user_id)
        if success:
            return {"message": "Video marked to keep forever", "video_id": video_id}
        raise HTTPException(status_code=404, detail="Video not found")

    elif action.action == "dismiss":
        # For dismiss, we just return success - frontend handles hiding
        return {"message": "Suggestion dismissed", "video_id": video_id}

    elif action.action == "delete":
        # Delete video
        from app.db.supabase_client import get_supabase_admin
        supabase = get_supabase_admin()

        result = supabase.table("douyin_videos").delete().eq("id", video_id).eq("user_id", auth.user_id).execute()

        if result.data:
            logger.info(f"Deleted video {video_id} via cleanup")
            return {"message": "Video deleted", "video_id": video_id}

        raise HTTPException(status_code=404, detail="Video not found")


@router.post("/batch")
async def batch_cleanup_action(
    action: CleanupBatchAction,
    auth: AuthDep = None,
):
    """
    Take action on multiple cleanup suggestions at once.

    Maximum 100 videos per batch.
    """
    if len(action.video_ids) > 100:
        raise HTTPException(
            status_code=400,
            detail="Maximum 100 videos per batch"
        )

    service = CleanupService()
    results = {"success": [], "failed": []}

    for video_id in action.video_ids:
        try:
            if action.action == "keep_forever":
                success = await service.mark_keep_forever(video_id, auth.user_id)
            elif action.action == "delete":
                from app.db.supabase_client import get_supabase_admin
                supabase = get_supabase_admin()
                result = supabase.table("douyin_videos").delete().eq("id", video_id).eq("user_id", auth.user_id).execute()
                success = len(result.data) > 0
            else:  # dismiss
                success = True

            if success:
                results["success"].append(video_id)
            else:
                results["failed"].append(video_id)

        except Exception as e:
            logger.error(f"Batch action failed for video {video_id}: {e}")
            results["failed"].append(video_id)

    return {
        "message": f"Processed {len(action.video_ids)} videos",
        "action": action.action,
        "success_count": len(results["success"]),
        "failed_count": len(results["failed"]),
        "failed_ids": results["failed"]
    }


@router.post("/videos/{video_id}/keep")
async def mark_keep_forever(
    video_id: int,
    auth: AuthDep = None,
):
    """Shortcut to mark a video as keep forever."""
    service = CleanupService()
    success = await service.mark_keep_forever(video_id, auth.user_id)

    if success:
        return {"message": "Video marked to keep forever", "video_id": video_id}

    raise HTTPException(status_code=404, detail="Video not found")


@router.delete("/videos/{video_id}/keep")
async def unmark_keep_forever(
    video_id: int,
    auth: AuthDep = None,
):
    """Remove keep forever mark from a video."""
    service = CleanupService()
    success = await service.unmark_keep_forever(video_id, auth.user_id)

    if success:
        return {"message": "Keep forever mark removed", "video_id": video_id}

    raise HTTPException(status_code=404, detail="Video not found")


@router.get("/storage")
async def get_storage_breakdown(auth: AuthDep = None):
    """
    Get storage usage breakdown.

    Shows storage usage by type, month, and tag.
    """
    from app.db.supabase_client import get_supabase_admin

    supabase = get_supabase_admin()

    # Get all videos with storage info
    result = supabase.table("douyin_videos").select(
        "id, storage_size, aweme_type, created_at"
    ).eq("user_id", auth.user_id).execute()

    videos = result.data

    # By type
    by_type = {"video": 0, "image": 0, "other": 0}
    for v in videos:
        aweme_type = v.get("aweme_type", 0)
        size = v.get("storage_size", 0) or 0
        if aweme_type in [0, 4, 61]:
            by_type["video"] += size
        elif aweme_type in [2, 68]:
            by_type["image"] += size
        else:
            by_type["other"] += size

    # By month
    from collections import defaultdict
    by_month = defaultdict(lambda: {"count": 0, "bytes": 0})
    for v in videos:
        if v.get("created_at"):
            month = v["created_at"][:7]  # YYYY-MM
            by_month[month]["count"] += 1
            by_month[month]["bytes"] += v.get("storage_size", 0) or 0

    by_month_list = [
        {"month": k, **v}
        for k, v in sorted(by_month.items(), reverse=True)
    ][:12]  # Last 12 months

    # Largest videos
    largest = sorted(
        [v for v in videos if v.get("storage_size")],
        key=lambda x: x.get("storage_size", 0),
        reverse=True
    )[:10]

    return {
        "by_type": by_type,
        "by_month": by_month_list,
        "largest_videos": largest,
        "total_bytes": sum(v.get("storage_size", 0) or 0 for v in videos),
        "total_videos": len(videos)
    }
