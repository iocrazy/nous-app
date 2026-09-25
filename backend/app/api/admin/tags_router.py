"""Admin API routes for Tag and Tag Group management."""

from typing import List, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.api.row_guard import require_row
from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.tags_repository import (
    AdminTagGroupNotFound,
    get_admin_tags_repository,
)
from app.schemas.admin_tags import (
    AdminTagBatchResult,
    AdminTagGroupListResponse,
    AdminTagGroupResponse,
    AdminTagListResponse,
    AdminTagResponse,
    AdminTagsOk,
)

router = APIRouter()


def _not_found() -> NoReturn:
    """Typed 404 (``details.code=not_found_or_out_of_scope``) for a write that
    named no row: an unknown id, a non-numeric one, or a batch/reorder list
    with any such id (those write nothing)."""
    require_row(None)
    raise AssertionError("unreachable")  # require_row(None) always raises


def _duplicate(what: str, name: str | None, e: IntegrityError) -> HTTPException:
    logger.info(f"[AdminTags] {what} refused, duplicate name: {e.orig}")
    return HTTPException(status_code=409, detail=f"{what} '{name}' already exists")


# ============================================
# Schemas
# ============================================


class AdminTagGroupCreate(BaseModel):
    name: str


class AdminTagGroupUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None


class TagGroupReorder(BaseModel):
    ids: List[str]


class AdminTagCreate(BaseModel):
    name: str
    name_zh: Optional[str] = None
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = None
    group_id: Optional[str] = None


class AdminTagUpdate(BaseModel):
    name: Optional[str] = None
    name_zh: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    group_id: Optional[str] = None
    sort_order: Optional[int] = None


class TagBatchAction(BaseModel):
    action: str  # "move" | "delete" | "color"
    tag_ids: List[str]
    group_id: Optional[str] = None  # for "move"
    color: Optional[str] = None  # for "color"


class TagReorder(BaseModel):
    group_id: Optional[str] = None
    tag_ids: List[str]


# ============================================
# Tag Group Endpoints
# ============================================


@router.get("/groups", response_model=AdminTagGroupListResponse)
async def list_groups(auth: AdminAuthDep):
    """List all tag groups with tag counts."""
    repo = get_admin_tags_repository()

    groups = await repo.list_groups()
    all_tags = await repo.all_tag_group_ids()

    group_counts: dict[str, int] = {}
    uncategorized = 0
    for t in all_tags:
        gid = t.get("group_id")
        if gid is None:
            uncategorized += 1
        else:
            group_counts[str(gid)] = group_counts.get(str(gid), 0) + 1

    for g in groups:
        g["tag_count"] = group_counts.get(str(g["id"]), 0)

    return {
        "success": True,
        "groups": groups,
        "total_tags": len(all_tags),
        "uncategorized_count": uncategorized,
    }


@router.post("/groups", response_model=AdminTagGroupResponse)
async def create_group(body: AdminTagGroupCreate, auth: AdminAuthDep):
    """Create a new tag group."""
    repo = get_admin_tags_repository()
    max_order = await repo.max_group_sort_order()
    try:
        group = await repo.create_group(body.name, max_order + 1)
    except IntegrityError as e:  # tag_groups_name_key
        raise _duplicate("Group", body.name, e) from e
    if not group:
        raise HTTPException(status_code=500, detail="Failed to create group")
    return {"success": True, "group": group}


@router.patch("/groups/{group_id}", response_model=AdminTagGroupResponse)
async def update_group(group_id: str, body: AdminTagGroupUpdate, auth: AdminAuthDep):
    """Update a tag group."""
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    repo = get_admin_tags_repository()
    try:
        group = await repo.update_group(group_id, update_data)
    except IntegrityError as e:  # tag_groups_name_key
        raise _duplicate("Group", body.name, e) from e
    return {"success": True, "group": require_row(group)}


@router.delete("/groups/{group_id}", response_model=AdminTagsOk)
async def delete_group(group_id: str, auth: AdminAuthDep):
    """Delete a tag group. Tags become uncategorized."""
    repo = get_admin_tags_repository()
    if not await repo.delete_group(group_id):
        _not_found()
    return {"success": True}


@router.post("/groups/reorder", response_model=AdminTagsOk)
async def reorder_groups(body: TagGroupReorder, auth: AdminAuthDep):
    """Bulk reorder groups. IDs list defines the new order.

    Any unknown or non-numeric id → typed 404 and nothing is reordered (same
    answer as the user-facing ``PUT /tags/groups/reorder``).
    """
    repo = get_admin_tags_repository()
    if not await repo.reorder_groups(body.ids):
        _not_found()
    return {"success": True}


# ============================================
# Tag Endpoints
# ============================================


@router.get("", response_model=AdminTagListResponse)
async def list_tags(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    group_id: Optional[str] = Query(
        None, description="Filter by group ID, use 'uncategorized' for NULL"
    ),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None, pattern="^(asc|desc)$"),
):
    """List all tags with pagination, search, and group filtering."""
    repo = get_admin_tags_repository()
    rows, total = await repo.list_tags(
        page=page,
        page_size=page_size,
        search=search,
        group_id=group_id,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    # Hydrate display fields
    tag_ids = [str(t["id"]) for t in rows]
    usage_counts = await repo.usage_counts(tag_ids)

    items = []
    for t in rows:
        item = {**t}
        item["group_name"] = (
            t.get("tag_groups", {}).get("name") if t.get("tag_groups") else None
        )
        item.pop("tag_groups", None)
        item["usage_count"] = usage_counts.get(str(t["id"]), 0)
        items.append(item)

    return {"success": True, "items": items, "total": total}


@router.post("", response_model=AdminTagResponse)
async def create_tag(body: AdminTagCreate, auth: AdminAuthDep):
    """Create a tag owned by the calling admin.

    There are no system tags any more (mig 468 dropped ``type='system'`` from
    ``tags_type_check`` and gave every user their own copy of the initial
    set), so an admin-made tag is an ordinary ``user`` tag in the admin's own
    library. This used to insert ``type='system', user_id=NULL``, which the
    CHECK rejects: every create from the admin console was a 500.
    """
    insert_data: dict = {
        "name": body.name,
        "type": "user",
        "color": body.color,
        "user_id": auth.user_id,
    }
    if body.name_zh:
        insert_data["name_zh"] = body.name_zh
    if body.icon:
        insert_data["icon"] = body.icon
    if body.group_id:
        insert_data["group_id"] = body.group_id

    repo = get_admin_tags_repository()
    try:
        tag = await repo.create_tag(insert_data)
    except AdminTagGroupNotFound:
        _not_found()
    except IntegrityError as e:
        # unique_tag_per_scope (name, type, user_id) — the admin already has
        # a tag with this name. Same answer as the user-facing POST /tags.
        logger.info(f"[AdminTags] create refused, duplicate name: {e.orig}")
        raise HTTPException(
            status_code=409, detail=f"Tag '{body.name}' already exists"
        ) from e
    if not tag:
        raise HTTPException(status_code=500, detail="Failed to create tag")
    return {"success": True, "tag": tag}


@router.patch("/{tag_id}", response_model=AdminTagResponse)
async def update_tag(tag_id: str, body: AdminTagUpdate, auth: AdminAuthDep):
    """Update a tag."""
    update_data: dict = {}
    for field in ["name", "name_zh", "color", "icon", "sort_order"]:
        val = getattr(body, field, None)
        if val is not None:
            update_data[field] = val

    # Allow explicitly setting group_id to null (move to uncategorized)
    if body.group_id is not None:
        update_data["group_id"] = body.group_id if body.group_id != "" else None

    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    repo = get_admin_tags_repository()
    try:
        tag = await repo.update_tag(tag_id, update_data)
    except AdminTagGroupNotFound:
        _not_found()
    except IntegrityError as e:  # unique_tag_per_scope
        raise _duplicate("Tag", body.name, e) from e
    return {"success": True, "tag": require_row(tag)}


@router.delete("/{tag_id}", response_model=AdminTagsOk)
async def delete_tag(tag_id: str, auth: AdminAuthDep):
    """Delete a tag and its resource associations."""
    repo = get_admin_tags_repository()
    if not await repo.delete_tag(tag_id):
        _not_found()
    return {"success": True}


@router.post("/batch", response_model=AdminTagBatchResult)
async def batch_action(body: TagBatchAction, auth: AdminAuthDep):
    """Batch operations on tags: move, delete, or change color.

    All-or-nothing: any unknown or non-numeric tag id (or an unknown target
    group for ``move``) → typed 404 and no tag is touched.
    """
    repo = get_admin_tags_repository()
    n = len(body.tag_ids)

    if body.action == "move":
        gid = body.group_id if body.group_id else None
        try:
            done = await repo.batch_set_group(body.tag_ids, gid)
        except AdminTagGroupNotFound:
            done = False
        message = f"Moved {n} tags"
    elif body.action == "delete":
        done = await repo.batch_delete(body.tag_ids)
        message = f"Deleted {n} tags"
    elif body.action == "color":
        if not body.color:
            raise HTTPException(
                status_code=400, detail="Color required for color action"
            )
        done = await repo.batch_set_color(body.tag_ids, body.color)
        message = f"Updated color for {n} tags"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    if not done:
        _not_found()
    return {"success": True, "message": message}


@router.post("/reorder", response_model=AdminTagsOk)
async def reorder_tags(body: TagReorder, auth: AdminAuthDep):
    """Reorder tags within a group. Any unknown or non-numeric id → typed 404
    and nothing is reordered."""
    repo = get_admin_tags_repository()
    if not await repo.reorder_tags(body.tag_ids):
        _not_found()
    return {"success": True}
