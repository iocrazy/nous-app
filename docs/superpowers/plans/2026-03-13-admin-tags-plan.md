# Admin Tags Management — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build admin tags management page with Eagle-style tag groups, CRUD, batch operations, and drag-and-drop sorting.

**Architecture:** New `tag_groups` table + `group_id`/`sort_order` columns on `tags`. Backend admin router with Supabase queries. Frontend page using existing NotionTable + @dnd-kit for reordering. Left sidebar for groups, right panel for tag table.

**Tech Stack:** FastAPI + Supabase (backend), React + Arco Design + @tanstack/react-table + @tanstack/react-query + @dnd-kit (frontend)

**Spec:** `docs/superpowers/specs/2026-03-13-admin-tags-design.md`

---

## File Structure

### Backend (new files)

| File | Responsibility |
|------|---------------|
| `supabase/migrations/101_tag_groups.sql` | Create `tag_groups` table, add `group_id`/`sort_order` to `tags` |
| `backend/app/api/admin/tags_router.py` | Admin tags + groups API endpoints |

### Backend (modified files)

| File | Change |
|------|--------|
| `backend/app/api/admin/__init__.py` | Register tags_router |

### Frontend (new files)

| File | Responsibility |
|------|---------------|
| `admin/src/api/endpoints/tags.ts` | API client: types, hooks for tags + groups |
| `admin/src/pages/tags/index.tsx` | Main page: left-right layout, state management |
| `admin/src/pages/tags/GroupSidebar.tsx` | Left sidebar: group list with DnD reorder |
| `admin/src/pages/tags/TagFormModal.tsx` | Create/edit tag modal form |

### Frontend (modified files)

| File | Change |
|------|--------|
| `admin/src/App.tsx` | Replace PlaceholderPage with TagsPage |
| `admin/package.json` | Add `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities` |

---

## Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/101_tag_groups.sql`

- [ ] **Step 1: Create migration file**

Create `supabase/migrations/101_tag_groups.sql`:

```sql
-- Tag Groups (Eagle-style tag categorization)
CREATE TABLE IF NOT EXISTS tag_groups (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  name VARCHAR(50) NOT NULL UNIQUE,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Add group_id and sort_order to tags
ALTER TABLE tags ADD COLUMN IF NOT EXISTS group_id BIGINT REFERENCES tag_groups(id) ON DELETE SET NULL;
ALTER TABLE tags ADD COLUMN IF NOT EXISTS sort_order INT DEFAULT 0;

-- Index for efficient group filtering
CREATE INDEX IF NOT EXISTS idx_tags_group_id ON tags(group_id);
CREATE INDEX IF NOT EXISTS idx_tag_groups_sort ON tag_groups(sort_order);

-- RLS: tag_groups readable by all authenticated, writable by admin only
ALTER TABLE tag_groups ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Tag groups visible to all authenticated" ON tag_groups
  FOR SELECT TO authenticated USING (true);

-- Admin writes handled via service_role key (bypasses RLS)
```

- [ ] **Step 2: Execute migration**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/101_tag_groups.sql
```

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/101_tag_groups.sql
git commit -m "feat(db): add tag_groups table and group_id/sort_order to tags"
```

---

## Task 2: Backend Admin Tags Router

**Files:**
- Create: `backend/app/api/admin/tags_router.py`
- Modify: `backend/app/api/admin/__init__.py`

- [ ] **Step 1: Create tags_router.py**

Create `backend/app/api/admin/tags_router.py`:

```python
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
    sort_order: Optional[str] = Query(None, regex="^(asc|desc)$"),
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
            .select("tag_id", count="exact")
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
    for field in ["name", "name_zh", "color", "icon", "group_id", "sort_order"]:
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
```

- [ ] **Step 2: Register router in admin __init__.py**

Add to `backend/app/api/admin/__init__.py`:

Import:
```python
from .tags_router import router as tags_router
```

Registration (after credits_router line):
```python
admin_router.include_router(tags_router, prefix="/tags", tags=["Admin - Tags"])
```

- [ ] **Step 3: Verify backend starts**

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8080
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/admin/tags_router.py backend/app/api/admin/__init__.py
git commit -m "feat(admin): add tags and tag groups API endpoints"
```

---

## Task 3: Frontend API Endpoints

**Files:**
- Create: `admin/src/api/endpoints/tags.ts`

- [ ] **Step 1: Create tags.ts**

Create `admin/src/api/endpoints/tags.ts`:

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

// --- Types ---

export interface TagGroup {
  id: string
  name: string
  sort_order: number
  tag_count: number
  created_at: string
}

export interface TagData {
  id: string
  name: string
  name_zh: string | null
  color: string | null
  icon: string | null
  type: string
  group_id: string | null
  group_name: string | null
  sort_order: number
  usage_count: number
  created_at: string
}

interface GroupsResponse {
  success: boolean
  groups: TagGroup[]
  total_tags: number
  uncategorized_count: number
}

interface TagListResponse {
  success: boolean
  items: TagData[]
  total: number
}

// --- Tag Groups ---

export function useTagGroups() {
  return useQuery({
    queryKey: ['admin-tag-groups'],
    queryFn: async () => {
      const { data } = await apiClient.get<GroupsResponse>('/api/v1/admin/tags/groups')
      return data
    },
  })
}

export function useCreateGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (name: string) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/groups', { name })
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tag-groups'] }),
  })
}

export function useUpdateGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, values }: { id: string; values: { name?: string; sort_order?: number } }) => {
      const { data } = await apiClient.patch(`/api/v1/admin/tags/groups/${id}`, values)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tag-groups'] }),
  })
}

export function useDeleteGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/tags/groups/${id}`)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
    },
  })
}

export function useReorderGroups() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (ids: string[]) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/groups/reorder', { ids })
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tag-groups'] }),
  })
}

// --- Tags ---

export function useCreateTag() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (values: {
      name: string
      name_zh?: string
      color?: string
      icon?: string
      group_id?: string
    }) => {
      const { data } = await apiClient.post('/api/v1/admin/tags', values)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useUpdateTag() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, values }: { id: string; values: Record<string, unknown> }) => {
      const { data } = await apiClient.patch(`/api/v1/admin/tags/${id}`, values)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useDeleteTag() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/tags/${id}`)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useBatchTagAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: {
      action: 'move' | 'delete' | 'color'
      tag_ids: string[]
      group_id?: string
      color?: string
    }) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/batch', body)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useReorderTags() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { group_id?: string; tag_ids: string[] }) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/reorder', body)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tags'] }),
  })
}
```

- [ ] **Step 2: Commit**

```bash
git add admin/src/api/endpoints/tags.ts
git commit -m "feat(admin): add tag and tag group API client hooks"
```

---

## Task 4: Install DnD Kit

**Files:**
- Modify: `admin/package.json`

- [ ] **Step 1: Install dependencies**

```bash
cd admin && npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
```

- [ ] **Step 2: Commit**

```bash
git add admin/package.json admin/package-lock.json
git commit -m "chore(admin): add @dnd-kit for drag-and-drop"
```

---

## Task 5: GroupSidebar Component

**Files:**
- Create: `admin/src/pages/tags/GroupSidebar.tsx`

- [ ] **Step 1: Create GroupSidebar.tsx**

Create `admin/src/pages/tags/GroupSidebar.tsx`:

```tsx
import { useState } from 'react'
import {
  Input,
  Menu,
  Message,
  Modal,
  Space,
  Typography,
} from '@arco-design/web-react'
import {
  IconPlus,
  IconTag,
  IconApps,
  IconFolder,
} from '@arco-design/web-react/icon'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { TagGroup } from '../../api/endpoints/tags'

interface GroupSidebarProps {
  groups: TagGroup[]
  totalTags: number
  uncategorizedCount: number
  selectedGroup: string | null // null = "all", "uncategorized", or group ID
  onSelect: (groupId: string | null) => void
  onCreateGroup: (name: string) => void
  onRenameGroup: (id: string, name: string) => void
  onDeleteGroup: (id: string) => void
  onReorderGroups: (ids: string[]) => void
}

function SortableGroupItem({
  group,
  isSelected,
  onSelect,
  onRename,
  onDelete,
}: {
  group: TagGroup
  isSelected: boolean
  onSelect: () => void
  onRename: (name: string) => void
  onDelete: () => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: group.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      onContextMenu={(e) => {
        e.preventDefault()
        Modal.confirm({
          title: 'Group Actions',
          content: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  Modal.destroyAll()
                  const newName = prompt('Rename group:', group.name)
                  if (newName && newName !== group.name) onRename(newName)
                }}
              >
                Rename
              </Typography.Text>
              <Typography.Text
                type="error"
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  Modal.destroyAll()
                  Modal.confirm({
                    title: 'Delete Group',
                    content: `Delete "${group.name}"? Tags will become uncategorized.`,
                    okButtonProps: { status: 'danger' },
                    onOk: onDelete,
                  })
                }}
              >
                Delete
              </Typography.Text>
            </Space>
          ),
          footer: null,
        })
      }}
    >
      <Menu.Item key={group.id} onClick={onSelect}>
        <span {...attributes} {...listeners} style={{ cursor: 'grab', marginRight: 6 }}>
          ⠿
        </span>
        <IconFolder style={{ marginRight: 6 }} />
        {group.name}
        <span style={{ float: 'right', color: 'var(--color-text-3)', fontSize: 12 }}>
          {group.tag_count}
        </span>
      </Menu.Item>
    </div>
  )
}

export function GroupSidebar({
  groups,
  totalTags,
  uncategorizedCount,
  selectedGroup,
  onSelect,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onReorderGroups,
}: GroupSidebarProps) {
  const [isCreating, setIsCreating] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = groups.findIndex((g) => g.id === active.id)
    const newIndex = groups.findIndex((g) => g.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    const reordered = [...groups]
    const [moved] = reordered.splice(oldIndex, 1)
    reordered.splice(newIndex, 0, moved)
    onReorderGroups(reordered.map((g) => g.id))
  }

  const handleCreateSubmit = () => {
    const name = newGroupName.trim()
    if (!name) return
    onCreateGroup(name)
    setNewGroupName('')
    setIsCreating(false)
  }

  return (
    <div style={{ width: 220, borderRight: '1px solid var(--color-border)', height: '100%', overflow: 'auto' }}>
      <Menu
        selectedKeys={selectedGroup === null ? ['all'] : [selectedGroup]}
        style={{ background: 'transparent' }}
      >
        <Menu.Item key="all" onClick={() => onSelect(null)}>
          <IconApps style={{ marginRight: 6 }} />
          All
          <span style={{ float: 'right', color: 'var(--color-text-3)', fontSize: 12 }}>
            {totalTags}
          </span>
        </Menu.Item>
        <Menu.Item key="uncategorized" onClick={() => onSelect('uncategorized')}>
          <IconTag style={{ marginRight: 6 }} />
          Uncategorized
          <span style={{ float: 'right', color: 'var(--color-text-3)', fontSize: 12 }}>
            {uncategorizedCount}
          </span>
        </Menu.Item>
      </Menu>

      <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Groups ({groups.length})
        </Typography.Text>
        <IconPlus
          style={{ cursor: 'pointer', fontSize: 14 }}
          onClick={() => setIsCreating(true)}
        />
      </div>

      {isCreating && (
        <div style={{ padding: '0 12px 8px' }}>
          <Input
            size="small"
            autoFocus
            placeholder="Group name"
            value={newGroupName}
            onChange={setNewGroupName}
            onPressEnter={handleCreateSubmit}
            onBlur={() => {
              if (!newGroupName.trim()) setIsCreating(false)
            }}
          />
        </div>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={groups.map((g) => g.id)} strategy={verticalListSortingStrategy}>
          <Menu selectedKeys={selectedGroup && selectedGroup !== 'uncategorized' ? [selectedGroup] : []}>
            {groups.map((group) => (
              <SortableGroupItem
                key={group.id}
                group={group}
                isSelected={selectedGroup === group.id}
                onSelect={() => onSelect(group.id)}
                onRename={(name) => onRenameGroup(group.id, name)}
                onDelete={() => onDeleteGroup(group.id)}
              />
            ))}
          </Menu>
        </SortableContext>
      </DndContext>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add admin/src/pages/tags/GroupSidebar.tsx
git commit -m "feat(admin): add GroupSidebar component with drag-and-drop"
```

---

## Task 6: TagFormModal Component

**Files:**
- Create: `admin/src/pages/tags/TagFormModal.tsx`

- [ ] **Step 1: Create TagFormModal.tsx**

Create `admin/src/pages/tags/TagFormModal.tsx`:

```tsx
import { useEffect, useState } from 'react'
import {
  Modal,
  Form,
  Input,
  Select,
} from '@arco-design/web-react'
import type { TagData, TagGroup } from '../../api/endpoints/tags'

const PRESET_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6',
  '#3b82f6', '#6366f1', '#8b5cf6', '#ec4899', '#f472b6',
  '#64748b', '#84cc16', '#06b6d4', '#a855f7',
]

interface TagFormModalProps {
  visible: boolean
  tag: TagData | null // null = create, non-null = edit
  groups: TagGroup[]
  onSubmit: (values: {
    name: string
    name_zh?: string
    color?: string
    icon?: string
    group_id?: string
  }) => void
  onClose: () => void
}

export function TagFormModal({ visible, tag, groups, onSubmit, onClose }: TagFormModalProps) {
  const [form] = Form.useForm()
  const isEdit = tag !== null

  useEffect(() => {
    if (visible) {
      if (tag) {
        form.setFieldsValue({
          name: tag.name,
          name_zh: tag.name_zh || '',
          color: tag.color || '#6366f1',
          icon: tag.icon || '',
          group_id: tag.group_id || undefined,
        })
      } else {
        form.resetFields()
        form.setFieldsValue({ color: '#6366f1' })
      }
    }
  }, [visible, tag, form])

  const handleOk = async () => {
    try {
      const values = await form.validate()
      onSubmit({
        name: values.name,
        name_zh: values.name_zh || undefined,
        color: values.color,
        icon: values.icon || undefined,
        group_id: values.group_id || undefined,
      })
    } catch {
      // validation failed
    }
  }

  return (
    <Modal
      title={isEdit ? 'Edit Tag' : 'New Tag'}
      visible={visible}
      onOk={handleOk}
      onCancel={onClose}
      autoFocus={false}
      unmountOnExit
    >
      <Form form={form} layout="vertical">
        <Form.Item label="Name" field="name" rules={[{ required: true, message: 'Name is required' }]}>
          <Input placeholder="Tag name (English)" />
        </Form.Item>
        <Form.Item label="Chinese Name" field="name_zh">
          <Input placeholder="Optional Chinese name" />
        </Form.Item>
        <Form.Item label="Color" field="color">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {PRESET_COLORS.map((c) => (
              <div
                key={c}
                onClick={() => form.setFieldValue('color', c)}
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 6,
                  background: c,
                  cursor: 'pointer',
                  border: form.getFieldValue('color') === c ? '2px solid white' : '2px solid transparent',
                }}
              />
            ))}
          </div>
        </Form.Item>
        <Form.Item label="Icon" field="icon">
          <Input placeholder="Emoji icon (e.g. 🎵)" />
        </Form.Item>
        <Form.Item label="Group" field="group_id">
          <Select placeholder="Uncategorized" allowClear>
            {groups.map((g) => (
              <Select.Option key={g.id} value={g.id}>
                {g.name}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
      </Form>
    </Modal>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add admin/src/pages/tags/TagFormModal.tsx
git commit -m "feat(admin): add TagFormModal component"
```

---

## Task 7: Main Tags Page

**Files:**
- Create: `admin/src/pages/tags/index.tsx`
- Modify: `admin/src/App.tsx`

- [ ] **Step 1: Create main tags page**

Create `admin/src/pages/tags/index.tsx`:

```tsx
import { useMemo, useState } from 'react'
import {
  Button,
  Message,
  Modal,
  Select,
  Space,
  Tag,
} from '@arco-design/web-react'
import {
  IconPlus,
  IconMore,
  IconDelete,
  IconEdit,
  IconSwap,
} from '@arco-design/web-react/icon'
import { Dropdown, Menu } from '@arco-design/web-react'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import {
  useTagGroups,
  useCreateGroup,
  useUpdateGroup,
  useDeleteGroup,
  useReorderGroups,
  useCreateTag,
  useUpdateTag,
  useDeleteTag,
  useBatchTagAction,
  type TagData,
} from '../../api/endpoints/tags'
import { GroupSidebar } from './GroupSidebar'
import { TagFormModal } from './TagFormModal'

export function TagsPage() {
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingTag, setEditingTag] = useState<TagData | null>(null)
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([])

  // Groups
  const { data: groupsData } = useTagGroups()
  const createGroup = useCreateGroup()
  const updateGroup = useUpdateGroup()
  const deleteGroup = useDeleteGroup()
  const reorderGroups = useReorderGroups()

  // Tags
  const createTag = useCreateTag()
  const updateTag = useUpdateTag()
  const deleteTagMut = useDeleteTag()
  const batchAction = useBatchTagAction()

  const groups = groupsData?.groups ?? []
  const totalTags = groupsData?.total_tags ?? 0
  const uncategorizedCount = groupsData?.uncategorized_count ?? 0

  // --- Handlers ---

  const handleCreateTag = (values: Parameters<typeof createTag.mutate>[0]) => {
    createTag.mutate(values, {
      onSuccess: () => {
        Message.success('Tag created')
        setModalVisible(false)
      },
    })
  }

  const handleUpdateTag = (values: Parameters<typeof createTag.mutate>[0]) => {
    if (!editingTag) return
    updateTag.mutate(
      { id: editingTag.id, values },
      {
        onSuccess: () => {
          Message.success('Tag updated')
          setModalVisible(false)
          setEditingTag(null)
        },
      },
    )
  }

  const handleDeleteTag = (tag: TagData) => {
    Modal.confirm({
      title: 'Delete Tag',
      content: `Delete "${tag.name}"? This will remove it from all resources.`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        deleteTagMut.mutateAsync(tag.id, {
          onSuccess: () => Message.success('Tag deleted'),
        }),
    })
  }

  const handleBatchMove = (groupId: string | undefined) => {
    batchAction.mutate(
      { action: 'move', tag_ids: selectedTagIds, group_id: groupId },
      {
        onSuccess: () => {
          Message.success(`Moved ${selectedTagIds.length} tags`)
          setSelectedTagIds([])
        },
      },
    )
  }

  const handleBatchDelete = () => {
    Modal.confirm({
      title: 'Delete Tags',
      content: `Delete ${selectedTagIds.length} tags? This cannot be undone.`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        batchAction.mutateAsync(
          { action: 'delete', tag_ids: selectedTagIds },
          {
            onSuccess: () => {
              Message.success(`Deleted ${selectedTagIds.length} tags`)
              setSelectedTagIds([])
            },
          },
        ),
    })
  }

  // --- Column definitions ---

  const columns = useMemo<NotionColumnDef<TagData>[]>(
    () => [
      {
        key: 'name',
        header: 'Name',
        type: 'text',
        filterable: true,
        sortable: true,
        required: true,
        minSize: 160,
        cell: (row) => (
          <Space>
            <span
              style={{
                display: 'inline-block',
                width: 14,
                height: 14,
                borderRadius: 4,
                background: row.color || '#6366f1',
              }}
            />
            <span>{row.icon ? `${row.icon} ` : ''}{row.name}</span>
            {row.name_zh && (
              <span style={{ color: 'var(--color-text-3)', fontSize: 12 }}>
                ({row.name_zh})
              </span>
            )}
          </Space>
        ),
      },
      {
        key: 'type',
        header: 'Type',
        type: 'select',
        filterable: true,
        size: 100,
        filterOptions: [
          { label: 'System', value: 'system' },
          { label: 'User', value: 'user' },
          { label: 'Time', value: 'time' },
        ],
        cell: (row) => (
          <Tag size="small" color={row.type === 'system' ? 'blue' : row.type === 'time' ? 'green' : 'gray'}>
            {row.type}
          </Tag>
        ),
      },
      {
        key: 'group_name',
        header: 'Group',
        type: 'text',
        size: 130,
        cell: (row) => row.group_name || '-',
      },
      {
        key: 'usage_count',
        header: 'Used',
        type: 'number',
        sortable: true,
        size: 80,
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        sortable: true,
        size: 130,
        cell: (row) => new Date(row.created_at).toLocaleDateString(),
      },
      {
        key: 'actions',
        header: '',
        type: 'text',
        required: true,
        size: 60,
        cell: (row) => (
          <Dropdown
            droplist={
              <Menu>
                <Menu.Item
                  key="edit"
                  onClick={() => {
                    setEditingTag(row)
                    setModalVisible(true)
                  }}
                >
                  <Space><IconEdit /> Edit</Space>
                </Menu.Item>
                <Menu.Item
                  key="delete"
                  onClick={() => handleDeleteTag(row)}
                  style={{ color: 'rgb(var(--danger-6))' }}
                >
                  <Space><IconDelete /> Delete</Space>
                </Menu.Item>
              </Menu>
            }
            position="br"
          >
            <IconMore style={{ cursor: 'pointer', fontSize: 18 }} />
          </Dropdown>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  // --- NotionTable setup ---

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<TagData>({
    tableKey: 'admin-tags',
    columns,
    defaultSorts: [{ field: 'sort_order', direction: 'asc' }],
    fetchData: async ({ page, pageSize, filters, sorts, search }) => {
      const typeFilter = filters.find((f) => f.field === 'type')
      const sortBy = sorts[0]?.field
      const sortOrder = sorts[0]?.direction

      const { data } = await apiClient.get('/api/v1/admin/tags', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(selectedGroup && { group_id: selectedGroup }),
          ...(typeFilter?.value && { type: typeFilter.value }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  // --- Batch bar ---

  const batchBar = selectedTagIds.length > 0 && (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 16px',
        background: 'var(--color-fill-2)',
        borderRadius: 8,
        marginBottom: 8,
      }}
    >
      <span>{selectedTagIds.length} selected</span>
      <Select
        size="small"
        placeholder="Move to..."
        style={{ width: 150 }}
        allowClear
        onChange={(val) => handleBatchMove(val || undefined)}
      >
        <Select.Option value="">Uncategorized</Select.Option>
        {groups.map((g) => (
          <Select.Option key={g.id} value={g.id}>
            {g.name}
          </Select.Option>
        ))}
      </Select>
      <Button
        size="small"
        status="danger"
        icon={<IconDelete />}
        onClick={handleBatchDelete}
      >
        Delete
      </Button>
      <Button
        size="small"
        type="text"
        onClick={() => setSelectedTagIds([])}
      >
        Clear
      </Button>
    </div>
  )

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 60px)' }}>
      <GroupSidebar
        groups={groups}
        totalTags={totalTags}
        uncategorizedCount={uncategorizedCount}
        selectedGroup={selectedGroup}
        onSelect={setSelectedGroup}
        onCreateGroup={(name) =>
          createGroup.mutate(name, {
            onSuccess: () => Message.success('Group created'),
          })
        }
        onRenameGroup={(id, name) =>
          updateGroup.mutate(
            { id, values: { name } },
            { onSuccess: () => Message.success('Group renamed') },
          )
        }
        onDeleteGroup={(id) =>
          deleteGroup.mutate(id, {
            onSuccess: () => {
              Message.success('Group deleted')
              if (selectedGroup === id) setSelectedGroup(null)
            },
          })
        }
        onReorderGroups={(ids) => reorderGroups.mutate(ids)}
      />

      <div style={{ flex: 1, overflow: 'auto', padding: '0 4px' }}>
        {batchBar}
        <NotionTable<TagData>
          table={table}
          toolbarProps={toolbarProps}
          pagination={pagination}
          onPageChange={setPage}
          isLoading={isLoading}
          title="Tags"
          emptyText="No tags found"
          scrollX={700}
          headerExtra={
            <Button
              type="primary"
              icon={<IconPlus />}
              size="small"
              onClick={() => {
                setEditingTag(null)
                setModalVisible(true)
              }}
            >
              New Tag
            </Button>
          }
        />
      </div>

      <TagFormModal
        visible={modalVisible}
        tag={editingTag}
        groups={groups}
        onSubmit={editingTag ? handleUpdateTag : handleCreateTag}
        onClose={() => {
          setModalVisible(false)
          setEditingTag(null)
        }}
      />
    </div>
  )
}
```

- [ ] **Step 2: Update App.tsx route**

In `admin/src/App.tsx`:

Replace the import:
```tsx
// Remove: import { PlaceholderPage } from './pages/placeholder'
// Add:
import { TagsPage } from './pages/tags'
```

Replace the route:
```tsx
// Change: <Route path="/tags" element={<PlaceholderPage title="Tags" />} />
// To:
<Route path="/tags" element={<TagsPage />} />
```

Note: Keep `PlaceholderPage` import if it's still used by `/api-keys` route.

- [ ] **Step 3: Verify build**

```bash
cd admin && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add admin/src/pages/tags/index.tsx admin/src/App.tsx
git commit -m "feat(admin): add Tags page with group sidebar and NotionTable"
```

---

## Task 8: Integration Test

- [ ] **Step 1: Run backend + admin frontend locally**

```bash
# Terminal 1: Backend
cd backend && uv run uvicorn app.main:app --reload --port 8080

# Terminal 2: Admin
cd admin && npm run dev
```

- [ ] **Step 2: Manual verification checklist**

1. Navigate to admin `/tags` page
2. Left sidebar shows "All", "Uncategorized", empty groups list
3. Create a group via [+] → type name → Enter
4. Group appears in sidebar with count 0
5. Click "New Tag" → fill form → select group → submit
6. Tag appears in table with correct group
7. Click group in sidebar → table filters to that group
8. Edit tag via ⋮ → Edit → change fields → save
9. Delete tag via ⋮ → Delete → confirm
10. Right-click group → Rename / Delete works
11. Drag groups to reorder → order persists after refresh

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(admin): complete tags management with groups, CRUD, and batch operations"
```
