"""API routes for Tags management."""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.repositories.hotspots_repository import get_hotspots_repository
from app.repositories.note_tags_repository import get_note_tags_repository
from app.repositories.tag_preferences_repository import (
    get_tag_preferences_repository,
)
from app.repositories.tags_repository import get_tags_repository
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


class MergeTagsRequest(BaseModel):
    target_id: str = Field(..., description="Surviving tag id")
    source_ids: List[str] = Field(
        ..., min_length=1, description="Tag ids to merge away (deleted)"
    )


class MergeTagsResponse(BaseModel):
    target_id: str
    resource_count: int


@router.get("/groups", response_model=TagGroupsListResponse)
async def list_tag_groups(auth: AuthDep):
    """List all tag groups (for frontend tag picker grouping)."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TagGroups

    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(TagGroups.id, TagGroups.name, TagGroups.sort_order).order_by(
                        TagGroups.sort_order
                    )
                )
            )
            .mappings()
            .all()
        )
    groups = [
        TagGroupItem(id=str(g["id"]), name=g["name"], sort_order=g["sort_order"])
        for g in rows
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
    from sqlalchemy import insert, select

    from app.db.session import read_scope, write_scope
    from app.models import TagGroups

    # Get max sort_order
    async with read_scope() as session:
        max_order = (
            await session.execute(
                select(TagGroups.sort_order)
                .order_by(TagGroups.sort_order.desc())
                .limit(1)
            )
        ).scalar() or 0
    async with write_scope() as session:
        g = (
            (
                await session.execute(
                    insert(TagGroups)
                    .values(name=body.name, sort_order=max_order + 1)
                    .returning(TagGroups.id, TagGroups.name, TagGroups.sort_order)
                )
            )
            .mappings()
            .first()
        )
    if not g:
        raise HTTPException(status_code=500, detail="Failed to create tag group")
    return TagGroupItem(id=str(g["id"]), name=g["name"], sort_order=g["sort_order"])


class TagGroupUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


# ⚠️ Declared BEFORE ``/groups/{group_id}``. Starlette matches routes in
# registration order, so with the literal route second every
# ``PUT /groups/reorder`` was swallowed by the rename route as
# ``group_id="reorder"`` and died at body validation (422 "name required").
# Group drag-reorder was dead in prod until 2026-08-26. Keep literal paths
# above their parameterised siblings.
class TagGroupReorderRequest(BaseModel):
    group_ids: List[str] = Field(..., description="Ordered list of group IDs")


@router.put("/groups/reorder")
async def reorder_tag_groups(auth: AuthDep, body: TagGroupReorderRequest):
    """Update sort_order for all groups based on the provided order."""
    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import TagGroups

    async with write_scope() as session:
        for idx, group_id in enumerate(body.group_ids):
            await session.execute(
                update(TagGroups)
                .where(TagGroups.id == int(group_id))
                .values(sort_order=idx)
            )
    return {"success": True}


@router.put("/groups/{group_id}", response_model=TagGroupItem)
async def rename_tag_group(auth: AuthDep, group_id: str, body: TagGroupUpdate):
    """Rename a tag group."""
    body.name = _validate_group_name(body.name)
    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import TagGroups

    async with write_scope() as session:
        g = (
            (
                await session.execute(
                    update(TagGroups)
                    .where(TagGroups.id == int(group_id))
                    .values(name=body.name)
                    .returning(TagGroups.id, TagGroups.name, TagGroups.sort_order)
                )
            )
            .mappings()
            .first()
        )
    if not g:
        raise HTTPException(status_code=404, detail="Tag group not found")
    return TagGroupItem(id=str(g["id"]), name=g["name"], sort_order=g["sort_order"])


@router.delete("/groups/{group_id}", status_code=status.HTTP_200_OK)
async def delete_tag_group(auth: AuthDep, group_id: str):
    """Delete a tag group. Tags in this group become uncategorized."""
    from sqlalchemy import delete, update

    from app.db.session import write_scope
    from app.models import TagGroups, Tags

    async with write_scope() as session:
        # Move tags to uncategorized (set group_id to NULL)
        await session.execute(
            update(Tags).where(Tags.group_id == int(group_id)).values(group_id=None)
        )
        # Delete the group
        await session.execute(delete(TagGroups).where(TagGroups.id == int(group_id)))
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
    repo = get_tags_repository()
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
    Get cross-domain tag usage statistics for the current user.

    Each item's ``count`` is resource usage (unchanged), decorated with
    ``notes`` (live inspiration notes) and ``hotspots`` (word hits in the recent
    hotspot window). The three sources are read concurrently.
    """
    user_id = auth.user_id
    repo = get_tags_repository()

    try:
        tag_counts = await repo.get_tag_counts(user_id, limit)

        # get_tag_counts (RPC + fallback) omits name_zh — backfill it in one
        # in-list query so Chinese hotspot words still match (never N+1).
        missing_zh = [int(t["id"]) for t in tag_counts if "name_zh" not in t]
        if missing_zh:
            zh_map = await repo.get_name_zh_map(missing_zh)
            for t in tag_counts:
                if "name_zh" not in t:
                    t["name_zh"] = zh_map.get(int(t["id"]))

        note_counts_result, hotspot_words_result = await asyncio.gather(
            get_note_tags_repository().counts_for_user(user_id),
            get_hotspots_repository().recent_tag_word_counts(),
            return_exceptions=True,
        )
        if isinstance(note_counts_result, Exception):
            logger.error(
                f"Failed to load note tag counts for tag statistics "
                f"(user_id={user_id}): {note_counts_result}"
            )
            note_counts = {}
        else:
            note_counts = note_counts_result
        if isinstance(hotspot_words_result, Exception):
            logger.error(
                f"Failed to load hotspot word counts for tag statistics "
                f"(user_id={user_id}): {hotspot_words_result}"
            )
            hotspot_words = {}
        else:
            hotspot_words = hotspot_words_result
        for t in tag_counts:
            words = {
                (t.get("name") or "").lower(),
                (t.get("name_zh") or "").lower(),
            } - {""}
            t["notes"] = note_counts.get(int(t["id"]), 0)
            t["hotspots"] = sum(hotspot_words.get(w, 0) for w in words)

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
    repo = get_tags_repository()

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
        group_id=tag.group_id,
        prompt_trigger=tag.prompt_trigger,
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
    repo = get_tags_repository()
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


@router.post("/merge", response_model=MergeTagsResponse)
async def merge_tags(auth: AuthDep, body: MergeTagsRequest):
    """Merge source tags into the target tag. User tags only; ownership and
    atomicity are enforced by the merge_tags RPC."""
    if body.target_id in body.source_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target cannot also be a source",
        )
    repo = get_tags_repository()
    try:
        count = await repo.merge_tags(body.target_id, body.source_ids, auth.user_id)
    except Exception as e:  # RPC RAISE EXCEPTION -> 400
        logger.warning(f"merge_tags failed: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return MergeTagsResponse(target_id=body.target_id, resource_count=count)


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
    repo = get_tags_repository()

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

    # ``group_id`` is nullable-with-meaning: null = "move to Uncategorized".
    # Forwarding it unconditionally would make an omitted field look identical
    # to an explicit null, so only pass it through when the client set it.
    group_kwargs = (
        {"group_id": tag_update.group_id}
        if "group_id" in tag_update.model_fields_set
        else {}
    )

    updated = await repo.update_tag(
        tag_id=tag_id,
        user_id=user_id,
        name=tag_update.name,
        name_zh=tag_update.name_zh,
        color=tag_update.color,
        icon=tag_update.icon,
        enabled=tag_update.enabled,
        # Promote-only: schema restricts this to "curated"; the repo's
        # _TAG_ATTRS whitelist already admits the ``origin`` column.
        origin=tag_update.origin,
        prompt_trigger=tag_update.prompt_trigger,
        **group_kwargs,
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
    repo = get_tags_repository()

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
