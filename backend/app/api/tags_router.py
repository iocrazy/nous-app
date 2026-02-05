"""API routes for Tags management."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.tags_repository import TagsRepository
from app.schemas.tags import (
    TagCreate,
    TagUpdate,
    TagResponse,
    TagListResponse,
    VideoTagCreate,
    VideoTagsResponse,
    VideoTagResponse,
    TagStatisticsResponse,
    TagCountItem,
)

router = APIRouter(prefix="/tags", tags=["Tags"])


@router.get("", response_model=TagListResponse)
async def list_tags(
    type_filter: Optional[str] = Query(None, description="Filter by tag type: system, user, time"),
    auth: AuthDep = None,
):
    """
    List all available tags.
    Returns system tags and user's own tags.
    """
    user_id = auth.user_id
    repo = TagsRepository()
    tags = await repo.get_all_tags(user_id)

    if type_filter:
        tags = [t for t in tags if t["type"] == type_filter]

    return TagListResponse(tags=tags, total=len(tags))


@router.get("/statistics", response_model=TagStatisticsResponse)
async def get_tag_statistics(
    limit: int = Query(10, ge=1, le=50, description="Number of top tags to return"),
    auth: AuthDep = None,
):
    """
    Get tag usage statistics for the current user.
    Returns top tags sorted by video count.
    """
    user_id = auth.user_id
    repo = TagsRepository()

    try:
        tag_counts = await repo.get_tag_counts(user_id, limit)
        total_tagged = sum(t.get('count', 0) for t in tag_counts)

        return TagStatisticsResponse(
            success=True,
            top_tags=[TagCountItem(**t) for t in tag_counts],
            total_tagged_videos=total_tagged
        )
    except Exception as e:
        logger.error(f"Failed to get tag statistics: {e}")
        return TagStatisticsResponse(
            success=False,
            top_tags=[],
            total_tagged_videos=0
        )


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    tag: TagCreate,
    auth: AuthDep = None,
):
    """
    Create a new user tag.
    System tags cannot be created via API.
    """
    user_id = auth.user_id
    repo = TagsRepository()

    # Check if tag with same name exists
    existing = await repo.get_tag_by_name(tag.name, user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tag '{tag.name}' already exists"
        )

    created_tag = await repo.create_tag(
        name=tag.name,
        user_id=user_id,
        color=tag.color,
        icon=tag.icon,
        name_zh=tag.name_zh
    )

    return created_tag


@router.get("/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    auth: AuthDep = None,
):
    """Get a specific tag by ID."""
    repo = TagsRepository()
    tag = await repo.get_tag_by_id(tag_id)

    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )

    # Check access: system/time tags are public, user tags must belong to user
    if tag["type"] == "user" and tag["user_id"] != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return tag


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: str,
    tag_update: TagUpdate,
    auth: AuthDep = None,
):
    """
    Update a user tag.
    Only user tags can be updated. System tags are immutable.
    """
    user_id = auth.user_id
    repo = TagsRepository()

    # Check tag exists and is user's own
    existing = await repo.get_tag_by_id(tag_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )

    if existing["type"] != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update system tags"
        )

    if existing["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    updated = await repo.update_tag(
        tag_id=tag_id,
        user_id=user_id,
        name=tag_update.name,
        color=tag_update.color,
        icon=tag_update.icon
    )

    return updated


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: str,
    auth: AuthDep = None,
):
    """
    Delete a user tag.
    Only user tags can be deleted. System tags cannot be deleted.
    """
    user_id = auth.user_id
    repo = TagsRepository()

    existing = await repo.get_tag_by_id(tag_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )

    if existing["type"] != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete system tags"
        )

    deleted = await repo.delete_tag(tag_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )


# Video-Tag association endpoints

@router.get("/videos/{video_id}/tags", response_model=VideoTagsResponse)
async def get_video_tags(
    video_id: int,
    auth: AuthDep = None,
):
    """Get all tags associated with a video."""
    repo = TagsRepository()
    tags_data = await repo.get_video_tags(video_id)

    tags = []
    for item in tags_data:
        if item.get("tags"):
            tags.append(VideoTagResponse(
                tag=item["tags"],
                confidence=item.get("confidence"),
                source=item.get("source", "manual"),
                created_at=item.get("created_at")
            ))

    return VideoTagsResponse(video_id=video_id, tags=tags)


@router.post("/videos/{video_id}/tags", status_code=status.HTTP_201_CREATED)
async def add_tag_to_video(
    video_id: int,
    video_tag: VideoTagCreate,
    auth: AuthDep = None,
):
    """Add a tag to a video."""
    repo = TagsRepository()
    result = await repo.add_tag_to_video(
        video_id=video_id,
        tag_id=str(video_tag.tag_id),
        confidence=video_tag.confidence,
        source=video_tag.source
    )

    return {"message": "Tag added successfully", "data": result}


@router.delete("/videos/{video_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag_from_video(
    video_id: int,
    tag_id: str,
    auth: AuthDep = None,
):
    """Remove a tag from a video."""
    repo = TagsRepository()
    removed = await repo.remove_tag_from_video(video_id, tag_id)

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag association not found"
        )
