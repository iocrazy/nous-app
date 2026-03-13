"""API routes for Tags management."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.tags_repository import TagsRepository
from app.schemas.tags import (
    TagCountItem,
    TagCreate,
    TagListResponse,
    TagResponse,
    TagStatisticsResponse,
    TagUpdate,
)

router = APIRouter(prefix="/tags", tags=["Tags"])


class TagGroupItem(BaseModel):
    id: str
    name: str
    sort_order: int


class TagGroupsListResponse(BaseModel):
    groups: List[TagGroupItem]


@router.get("/groups", response_model=TagGroupsListResponse)
async def list_tag_groups(auth: AuthDep = None):
    """List all tag groups (for frontend tag picker grouping)."""
    client = await get_async_supabase_admin()
    result = (
        await client.table("tag_groups")
        .select("id, name, sort_order")
        .order("sort_order")
        .execute()
    )
    groups = [
        TagGroupItem(id=str(g["id"]), name=g["name"], sort_order=g["sort_order"])
        for g in (result.data or [])
    ]
    return TagGroupsListResponse(groups=groups)


@router.get("", response_model=TagListResponse)
async def list_tags(
    type_filter: Optional[str] = Query(
        None, description="Filter by tag type: system, user, time"
    ),
    include_disabled: bool = Query(
        False, description="Include disabled tags (for settings page)"
    ),
    auth: AuthDep = None,
):
    """
    List all available tags.
    Returns system tags and user's own tags.
    By default only returns enabled tags; pass include_disabled=true for settings.
    """
    user_id = auth.user_id
    repo = TagsRepository()
    tags = await repo.get_all_tags(user_id, enabled_only=not include_disabled)

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
        total_tagged = sum(t.get("count", 0) for t in tag_counts)

        return TagStatisticsResponse(
            success=True,
            top_tags=[TagCountItem(**t) for t in tag_counts],
            total_tagged_videos=total_tagged,
        )
    except Exception as e:
        logger.error(f"Failed to get tag statistics: {e}")
        return TagStatisticsResponse(success=False, top_tags=[], total_tagged_videos=0)


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
            detail=f"Tag '{tag.name}' already exists",
        )

    created_tag = await repo.create_tag(
        name=tag.name,
        user_id=user_id,
        color=tag.color,
        icon=tag.icon,
        name_zh=tag.name_zh,
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
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    # Check access: system/time tags are public, user tags must belong to user
    if tag["type"] == "user" and tag["user_id"] != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    return tag


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: str,
    tag_update: TagUpdate,
    auth: AuthDep = None,
):
    """
    Update a tag.
    - For user tags: can update name, color, icon, enabled
    - For system tags: can only update enabled (visibility toggle)
    """
    user_id = auth.user_id
    repo = TagsRepository()

    existing = await repo.get_tag_by_id(tag_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    # System tags: only allow toggling 'enabled'
    if existing["type"] != "user":
        if tag_update.enabled is not None:
            updated = await repo.update_tag_admin(
                tag_id=tag_id, enabled=tag_update.enabled
            )
            return updated
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System tags can only toggle enabled status",
        )

    if existing["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    updated = await repo.update_tag(
        tag_id=tag_id,
        user_id=user_id,
        name=tag_update.name,
        color=tag_update.color,
        icon=tag_update.icon,
        enabled=tag_update.enabled,
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
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    if existing["type"] != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete system tags"
        )

    deleted = await repo.delete_tag(tag_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
