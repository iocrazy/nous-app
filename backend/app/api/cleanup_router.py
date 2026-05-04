"""API routes for Cleanup Suggestions."""

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.schemas.cleanup import (
    CleanupAction,
    CleanupBatchAction,
    CleanupDataResponse,
    CleanupStats,
)
from app.schemas.cleanup import CleanupSuggestion as CleanupSuggestionSchema
from app.schemas.cleanup import (
    CleanupSuggestionsResponse,
)
from app.services.cleanup_service import CleanupService

router = APIRouter(prefix="/cleanup", tags=["Cleanup"])


@router.get("/data", response_model=CleanupDataResponse)
async def get_cleanup_data(
    auth: AuthDep,
    limit: int = Query(50, ge=1, le=200),
    include_duplicates: bool = Query(
        True, description="Include potential duplicate detection"
    ),
):
    """
    Get all cleanup data in a single optimized call.

    Returns suggestions, stats, and categories together for better performance.
    This endpoint reduces network round trips by combining multiple queries.
    """
    service = CleanupService()

    data = await service.get_cleanup_data(
        user_id=auth.user_id, limit=limit, include_duplicates=include_duplicates
    )

    suggestions = data["suggestions"]
    stats = data["stats"]
    categories = data["categories"]

    total_reclaimable = sum(s.storage_size for s in suggestions)

    return CleanupDataResponse(
        suggestions=[
            CleanupSuggestionSchema(
                media_id=s.media_id,
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
                similarity_score=s.similarity_score,
            )
            for s in suggestions
        ],
        total_count=len(suggestions),
        total_reclaimable_bytes=total_reclaimable,
        categories=categories,
        stats=CleanupStats(
            total_videos=stats.get("total_videos", 0),
            total_storage_bytes=stats.get("total_storage_bytes", 0),
            videos_never_viewed=stats.get("videos_never_viewed", 0),
            videos_not_viewed_30_days=stats.get("videos_not_viewed_30_days", 0),
            potential_duplicates=categories.get("duplicate_content", 0),
            videos_marked_keep=stats.get("videos_marked_keep", 0),
            reclaimable_bytes=stats.get("reclaimable_bytes", 0),
        ),
    )


@router.get("/suggestions", response_model=CleanupSuggestionsResponse)
async def get_cleanup_suggestions(
    auth: AuthDep,
    limit: int = Query(50, ge=1, le=200),
    include_duplicates: bool = Query(
        True, description="Include potential duplicate detection"
    ),
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
        user_id=auth.user_id, limit=limit, include_duplicates=include_duplicates
    )

    total_reclaimable = sum(s.storage_size for s in suggestions)

    return CleanupSuggestionsResponse(
        suggestions=[
            CleanupSuggestionSchema(
                media_id=s.media_id,
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
                similarity_score=s.similarity_score,
            )
            for s in suggestions
        ],
        total_count=len(suggestions),
        total_reclaimable_bytes=total_reclaimable,
        categories=categories,
    )


@router.get("/stats", response_model=CleanupStats)
async def get_cleanup_stats(auth: AuthDep):
    """
    Get overall cleanup statistics.

    Provides an overview of your library's storage usage and cleanup potential.
    """
    service = CleanupService()
    stats = await service.get_cleanup_stats(auth.user_id)

    return CleanupStats(**stats)


@router.post("/media/{media_id}/action")
async def take_cleanup_action(
    auth: AuthDep,
    media_id: int,
    action: CleanupAction,
):
    """
    Take action on a cleanup suggestion.

    Actions:
    - **keep_forever**: Mark media item to never be suggested for cleanup
    - **dismiss**: Ignore this suggestion (temporary)
    - **delete**: Delete the media item (moves to trash or permanent delete)
    """
    service = CleanupService()

    if action.action == "keep_forever":
        success = await service.mark_keep_forever(media_id, auth.user_id)
        if success:
            return {"message": "Media marked to keep forever", "media_id": media_id}
        raise HTTPException(status_code=404, detail="Media not found")

    elif action.action == "dismiss":
        # For dismiss, we just return success - frontend handles hiding
        return {"message": "Suggestion dismissed", "media_id": media_id}

    elif action.action == "delete":
        # Delete media
        from app.db.supabase_client import get_async_supabase_admin

        supabase = await get_async_supabase_admin()

        result = (
            await supabase.table("parsed_media")
            .delete()
            .eq("id", media_id)
            .eq("user_id", auth.user_id)
            .execute()
        )

        if result.data:
            logger.info(f"Deleted media {media_id} via cleanup")
            return {"message": "Media deleted", "media_id": media_id}

        raise HTTPException(status_code=404, detail="Media not found")


@router.post("/batch")
async def batch_cleanup_action(
    auth: AuthDep,
    action: CleanupBatchAction,
):
    """
    Take action on multiple cleanup suggestions at once.

    Maximum 100 media items per batch.
    """
    if len(action.media_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 media items per batch")

    service = CleanupService()
    results = {"success": [], "failed": []}

    for media_id in action.media_ids:
        try:
            if action.action == "keep_forever":
                success = await service.mark_keep_forever(media_id, auth.user_id)
            elif action.action == "delete":
                from app.db.supabase_client import get_async_supabase_admin

                supabase = await get_async_supabase_admin()
                result = (
                    await supabase.table("parsed_media")
                    .delete()
                    .eq("id", media_id)
                    .eq("user_id", auth.user_id)
                    .execute()
                )
                success = len(result.data) > 0
            else:  # dismiss
                success = True

            if success:
                results["success"].append(media_id)
            else:
                results["failed"].append(media_id)

        except Exception as e:
            logger.exception(f"Batch action failed for media {media_id}: {e}")
            results["failed"].append(media_id)

    return {
        "message": f"Processed {len(action.media_ids)} media items",
        "action": action.action,
        "success_count": len(results["success"]),
        "failed_count": len(results["failed"]),
        "failed_ids": results["failed"],
    }


@router.post("/media/{media_id}/keep")
async def mark_keep_forever(
    auth: AuthDep,
    media_id: int,
):
    """Shortcut to mark a media item as keep forever."""
    service = CleanupService()
    success = await service.mark_keep_forever(media_id, auth.user_id)

    if success:
        return {"message": "Media marked to keep forever", "media_id": media_id}

    raise HTTPException(status_code=404, detail="Media not found")


@router.delete("/media/{media_id}/keep")
async def unmark_keep_forever(
    auth: AuthDep,
    media_id: int,
):
    """Remove keep forever mark from a media item."""
    service = CleanupService()
    success = await service.unmark_keep_forever(media_id, auth.user_id)

    if success:
        return {"message": "Keep forever mark removed", "media_id": media_id}

    raise HTTPException(status_code=404, detail="Media not found")


@router.get("/storage")
async def get_storage_breakdown(auth: AuthDep):
    """
    Get storage usage breakdown.

    Shows storage usage by type, month, and tag.
    """
    from app.db.supabase_client import get_async_supabase_admin

    supabase = await get_async_supabase_admin()

    # Get all videos with storage info
    result = (
        await supabase.table("parsed_media")
        .select("id, storage_size, media_type, created_at")
        .eq("user_id", auth.user_id)
        .execute()
    )

    videos = result.data

    # By type
    by_type = {"video": 0, "image": 0, "other": 0}
    for v in videos:
        media_type = v.get("media_type", "video")
        size = v.get("storage_size", 0) or 0
        if media_type in ["video", "special"]:
            by_type["video"] += size
        elif media_type in ["carousel", "image_text"]:
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
        {"month": k, **v} for k, v in sorted(by_month.items(), reverse=True)
    ][
        :12
    ]  # Last 12 months

    # Largest videos
    largest = sorted(
        [v for v in videos if v.get("storage_size")],
        key=lambda x: x.get("storage_size", 0),
        reverse=True,
    )[:10]

    return {
        "by_type": by_type,
        "by_month": by_month_list,
        "largest_videos": largest,
        "total_bytes": sum(v.get("storage_size", 0) or 0 for v in videos),
        "total_videos": len(videos),
    }
