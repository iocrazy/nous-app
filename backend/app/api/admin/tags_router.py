"""Admin API routes for Tag and Tag Group management."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin


router = APIRouter()


# ============================================
# Schemas
# ============================================


class TagGroupCreate(BaseModel):
    name: str


class TagGroupUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None


class TagGroupReorder(BaseModel):
    ids: List[str]


class TagCreate(BaseModel):
    name: str
    name_zh: Optional[str] = None
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = None
    group_id: Optional[str] = None


class TagUpdate(BaseModel):
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


@router.get("/groups")
async def list_groups(auth: AdminAuthDep):
    """List all tag groups with tag counts."""
    supabase = await get_async_supabase_admin()

    # Get groups ordered by sort_order
    result = await (
        supabase.table("tag_groups")
        .select("*")
        .order("sort_order")
        .order("created_at")
        .execute()
    )
    groups = result.data or []

    # Get tag counts per group
    tags_result = await (
        supabase.table("tags")
        .select("group_id")
        .execute()
    )
    all_tags = tags_result.data or []

    # Count tags per group + uncategorized + total
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


@router.post("/groups")
async def create_group(body: TagGroupCreate, auth: AdminAuthDep):
    """Create a new tag group."""
    supabase = await get_async_supabase_admin()

    # Get max sort_order
    existing = await (
        supabase.table("tag_groups")
        .select("sort_order")
        .order("sort_order", desc=True)
        .limit(1)
        .execute()
    )
    max_order = existing.data[0]["sort_order"] if existing.data else 0

    result = await (
        supabase.table("tag_groups")
        .insert({"name": body.name, "sort_order": max_order + 1})
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create group")

    return {"success": True, "group": result.data[0]}


@router.patch("/groups/{group_id}")
async def update_group(group_id: str, body: TagGroupUpdate, auth: AdminAuthDep):
    """Update a tag group."""
    supabase = await get_async_supabase_admin()

    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    result = await (
        supabase.table("tag_groups")
        .update(update_data)
        .eq("id", group_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Group not found")

    return {"success": True, "group": result.data[0]}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, auth: AdminAuthDep):
    """Delete a tag group. Tags become uncategorized."""
    supabase = await get_async_supabase_admin()

    # Tags' group_id is SET NULL on delete via FK constraint
    result = await (
        supabase.table("tag_groups")
        .delete()
        .eq("id", group_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Group not found")

    return {"success": True}


@router.post("/groups/reorder")
async def reorder_groups(body: TagGroupReorder, auth: AdminAuthDep):
    """Bulk reorder groups. IDs list defines the new order."""
    supabase = await get_async_supabase_admin()

    for idx, gid in enumerate(body.ids):
        await (
            supabase.table("tag_groups")
            .update({"sort_order": idx})
            .eq("id", gid)
            .execute()
        )

    return {"success": True}


# ============================================
# Tag Endpoints
# ============================================


@router.get("")
async def list_tags(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None, description="Filter by group ID, use 'uncategorized' for NULL"),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None, pattern="^(asc|desc)$"),
):
    """List all tags with pagination, search, and group filtering."""
    supabase = await get_async_supabase_admin()

    query = supabase.table("tags").select("*, tag_groups(name)", count="exact")

    # Filter by group
    if group_id == "uncategorized":
        query = query.is_("group_id", "null")
    elif group_id:
        query = query.eq("group_id", group_id)

    # Search
    if search:
        query = query.or_(f"name.ilike.%{search}%,name_zh.ilike.%{search}%")

    # Sort
    if sort_by and sort_order:
        query = query.order(sort_by, desc=(sort_order == "desc"))
    else:
        query = query.order("sort_order").order("created_at", desc=True)

    # Paginate
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    # Get usage counts for returned tags
    tag_ids = [str(t["id"]) for t in (result.data or [])]
    usage_counts: dict[str, int] = {}
    if tag_ids:
        counts_result = await (
            supabase.table("resource_tags")
            .select("tag_id")
            .in_("tag_id", tag_ids)
            .execute()
        )
        # Count per tag_id
        for row in (counts_result.data or []):
            tid = str(row["tag_id"])
            usage_counts[tid] = usage_counts.get(tid, 0) + 1

    items = []
    for t in (result.data or []):
        item = {**t}
        item["group_name"] = t.get("tag_groups", {}).get("name") if t.get("tag_groups") else None
        item.pop("tag_groups", None)
        item["usage_count"] = usage_counts.get(str(t["id"]), 0)
        items.append(item)

    return {
        "success": True,
        "items": items,
        "total": result.count or 0,
    }


@router.post("")
async def create_tag(body: TagCreate, auth: AdminAuthDep):
    """Create a new system tag."""
    supabase = await get_async_supabase_admin()

    insert_data = {
        "name": body.name,
        "type": "system",
        "color": body.color,
        "user_id": None,
    }
    if body.name_zh:
        insert_data["name_zh"] = body.name_zh
    if body.icon:
        insert_data["icon"] = body.icon
    if body.group_id:
        insert_data["group_id"] = body.group_id

    result = await supabase.table("tags").insert(insert_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create tag")

    return {"success": True, "tag": result.data[0]}


@router.patch("/{tag_id}")
async def update_tag(tag_id: str, body: TagUpdate, auth: AdminAuthDep):
    """Update a tag."""
    supabase = await get_async_supabase_admin()

    update_data = {}
    for field in ["name", "name_zh", "color", "icon", "sort_order"]:
        val = getattr(body, field, None)
        if val is not None:
            update_data[field] = val

    # Allow explicitly setting group_id to null (move to uncategorized)
    if body.group_id is not None:
        update_data["group_id"] = body.group_id if body.group_id != "" else None

    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    result = await (
        supabase.table("tags")
        .update(update_data)
        .eq("id", tag_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Tag not found")

    return {"success": True, "tag": result.data[0]}


@router.delete("/{tag_id}")
async def delete_tag(tag_id: str, auth: AdminAuthDep):
    """Delete a tag and its resource associations."""
    supabase = await get_async_supabase_admin()

    # Delete resource_tags associations first
    await supabase.table("resource_tags").delete().eq("tag_id", tag_id).execute()

    # Delete tag
    result = await supabase.table("tags").delete().eq("id", tag_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Tag not found")

    return {"success": True}


@router.post("/batch")
async def batch_action(body: TagBatchAction, auth: AdminAuthDep):
    """Batch operations on tags: move, delete, or change color."""
    supabase = await get_async_supabase_admin()

    if body.action == "move":
        gid = body.group_id if body.group_id else None
        for tid in body.tag_ids:
            await (
                supabase.table("tags")
                .update({"group_id": gid})
                .eq("id", tid)
                .execute()
            )
        return {"success": True, "message": f"Moved {len(body.tag_ids)} tags"}

    elif body.action == "delete":
        for tid in body.tag_ids:
            await supabase.table("resource_tags").delete().eq("tag_id", tid).execute()
            await supabase.table("tags").delete().eq("id", tid).execute()
        return {"success": True, "message": f"Deleted {len(body.tag_ids)} tags"}

    elif body.action == "color":
        if not body.color:
            raise HTTPException(status_code=400, detail="Color required for color action")
        for tid in body.tag_ids:
            await (
                supabase.table("tags")
                .update({"color": body.color})
                .eq("id", tid)
                .execute()
            )
        return {"success": True, "message": f"Updated color for {len(body.tag_ids)} tags"}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")


@router.post("/reorder")
async def reorder_tags(body: TagReorder, auth: AdminAuthDep):
    """Reorder tags within a group."""
    supabase = await get_async_supabase_admin()

    for idx, tid in enumerate(body.tag_ids):
        await (
            supabase.table("tags")
            .update({"sort_order": idx})
            .eq("id", tid)
            .execute()
        )

    return {"success": True}
