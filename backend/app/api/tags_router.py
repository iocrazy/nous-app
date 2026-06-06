"""API routes for Tags management."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.tag_preferences_repository import (
    get_tag_preferences_repository,
)
from app.repositories.tags_repository import TagsRepository
from app.schemas.tag_preferences import (
    TagPreferencesResponse,
    TagPreferencesUpdate,
)
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
async def list_tag_groups(auth: AuthDep):
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


class TagGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


# Names that collide with the frontend's "(no group)" sentinel — forbidden
# as real-group names so the sidebar / picker render unambiguously.
_RESERVED_GROUP_NAMES = {"uncategorized", "未分类"}


def _validate_group_name(name: str) -> str:
    cleaned = name.strip()
    if cleaned.lower() in _RESERVED_GROUP_NAMES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{cleaned}' is reserved for the built-in uncategorized bucket — pick a different name.",
        )
    return cleaned


@router.post(
    "/groups", response_model=TagGroupItem, status_code=status.HTTP_201_CREATED
)
async def create_tag_group(auth: AuthDep, body: TagGroupCreate):
    """Create a new tag group."""
    body.name = _validate_group_name(body.name)
    client = await get_async_supabase_admin()
    # Get max sort_order
    existing = (
        await client.table("tag_groups")
        .select("sort_order")
        .order("sort_order", desc=True)
        .limit(1)
        .execute()
    )
    max_order = existing.data[0]["sort_order"] if existing.data else 0
    result = (
        await client.table("tag_groups")
        .insert(
            {
                "name": body.name,
                "sort_order": max_order + 1,
            }
        )
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create tag group")
    g = result.data[0]
    return TagGroupItem(id=str(g["id"]), name=g["name"], sort_order=g["sort_order"])


class TagGroupUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


@router.put("/groups/{group_id}", response_model=TagGroupItem)
async def rename_tag_group(auth: AuthDep, group_id: str, body: TagGroupUpdate):
    """Rename a tag group."""
    body.name = _validate_group_name(body.name)
    client = await get_async_supabase_admin()
    result = (
        await client.table("tag_groups")
        .update({"name": body.name})
        .eq("id", group_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Tag group not found")
    g = result.data[0]
    return TagGroupItem(id=str(g["id"]), name=g["name"], sort_order=g["sort_order"])


@router.delete("/groups/{group_id}", status_code=status.HTTP_200_OK)
async def delete_tag_group(auth: AuthDep, group_id: str):
    """Delete a tag group. Tags in this group become uncategorized."""
    client = await get_async_supabase_admin()
    # Move tags to uncategorized (set group_id to NULL)
    await client.table("tags").update({"group_id": None}).eq(
        "group_id", group_id
    ).execute()
    # Delete the group
    await client.table("tag_groups").delete().eq("id", group_id).execute()
    return {"success": True}


class TagGroupReorderRequest(BaseModel):
    group_ids: List[str] = Field(..., description="Ordered list of group IDs")


@router.put("/groups/reorder")
async def reorder_tag_groups(auth: AuthDep, body: TagGroupReorderRequest):
    """Update sort_order for all groups based on the provided order."""
    client = await get_async_supabase_admin()
    for idx, group_id in enumerate(body.group_ids):
        await client.table("tag_groups").update({"sort_order": idx}).eq(
            "id", group_id
        ).execute()
    return {"success": True}


@router.get("", response_model=TagListResponse)
async def list_tags(
    auth: AuthDep,
    type_filter: Optional[str] = Query(
        None, description="Filter by tag type: system, user, time"
    ),
    enabled_only: bool = Query(
        False,
        description="If true, only return enabled tags (for Shortcuts/public API)",
    ),
):
    """
    List all available tags.
    Returns system tags and user's own tags.
    Pass enabled_only=true to filter out disabled tags.
    """
    user_id = auth.user_id
    repo = TagsRepository()
    tags = await repo.get_all_tags(user_id, enabled_only=enabled_only)

    if type_filter:
        tags = [t for t in tags if t["type"] == type_filter]

    return TagListResponse(tags=tags, total=len(tags))


@router.get("/statistics", response_model=TagStatisticsResponse)
async def get_tag_statistics(
    auth: AuthDep,
    limit: int = Query(10, ge=1, le=50, description="Number of top tags to return"),
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
    auth: AuthDep,
    tag: TagCreate,
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


@router.get("/preferences", response_model=TagPreferencesResponse)
async def get_tag_preferences(auth: AuthDep):
    """Get current user's tag picker preferences."""
    repo = get_tag_preferences_repository()
    prefs = await repo.get_preferences(auth.user_id)
    return TagPreferencesResponse(**prefs)


@router.patch("/preferences", response_model=TagPreferencesResponse)
async def update_tag_preferences(
    auth: AuthDep,
    request: TagPreferencesUpdate,
):
    """Update tag picker preferences (partial merge)."""
    repo = get_tag_preferences_repository()
    updated = await repo.upsert_preferences(
        auth.user_id,
        request.model_dump(exclude_none=True),
    )
    return TagPreferencesResponse(**updated)


@router.get("/{tag_id}", response_model=TagResponse)
async def get_tag(
    auth: AuthDep,
    tag_id: str,
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
    auth: AuthDep,
    tag_id: str,
    tag_update: TagUpdate,
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
        name_zh=tag_update.name_zh,
        color=tag_update.color,
        icon=tag_update.icon,
        enabled=tag_update.enabled,
        group_id=tag_update.group_id,
    )

    return updated


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    auth: AuthDep,
    tag_id: str,
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
