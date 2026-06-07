# Admin Tags Management — Design

## Overview

Admin-level system tag management with Eagle-style tag groups. Left sidebar shows groups (All / Uncategorized / custom groups), right side shows tags in the selected group using NotionTable. Supports CRUD, batch operations, and drag-and-drop sorting.

## Scope (Phase 1)

- Tag Groups: CRUD, drag-and-drop reorder
- System Tags: CRUD within groups, batch move/delete/color, drag-and-drop sort within group
- Admin-only (requires `AdminAuthDep`)

## Database Changes

### New table: `tag_groups`

```sql
CREATE TABLE tag_groups (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  name VARCHAR(50) NOT NULL UNIQUE,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### Alter `tags` table

```sql
ALTER TABLE tags ADD COLUMN group_id BIGINT REFERENCES tag_groups(id) ON DELETE SET NULL;
ALTER TABLE tags ADD COLUMN sort_order INT DEFAULT 0;
```

- `group_id = NULL` means "Uncategorized"
- `sort_order` for drag-and-drop ordering within a group

## Backend API

New router: `backend/app/api/admin/tags_router.py`

All endpoints require `AdminAuthDep`.

### Tag Groups

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/tags/groups` | GET | List all groups with tag count |
| `/admin/tags/groups` | POST | Create group `{ name }` |
| `/admin/tags/groups/{id}` | PATCH | Update group `{ name, sort_order }` |
| `/admin/tags/groups/{id}` | DELETE | Delete group (tags become Uncategorized) |
| `/admin/tags/groups/reorder` | POST | Bulk reorder `{ ids: [id1, id2, ...] }` |

### Tags

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/tags` | GET | List tags (params: group_id, search, page, page_size, sort_by, sort_order) |
| `/admin/tags` | POST | Create system tag `{ name, name_zh, color, icon, group_id }` |
| `/admin/tags/{id}` | PATCH | Update tag fields |
| `/admin/tags/{id}` | DELETE | Delete tag (removes resource_tags associations) |
| `/admin/tags/batch` | POST | Batch operation `{ action: "move"|"delete"|"color", tag_ids: [...], group_id?, color? }` |
| `/admin/tags/reorder` | POST | Reorder within group `{ group_id, tag_ids: [id1, id2, ...] }` |

### Response format

```json
{
  "success": true,
  "items": [...],
  "total": 100
}
```

## Frontend Layout

```
┌─────────────────────────────────────────────────────┐
│ Tags                                    [+ New Tag] │
├───────────────┬─────────────────────────────────────┤
│ All (120)     │ NotionTable                          │
│ Uncat. (80)   │ ☑ [Name] [Color] [Group] [Used] [⋮]│
│ ──────────── │ ☐  平板   ████   数码    1       ⋮  │
│ Groups    [+] │ ☐  手机   ████   数码    3       ⋮  │
│  ⠿ 数码产品 10│ ☐  音箱   ████   数码    1       ⋮  │
│  ⠿ 生活用品  5│                                      │
│  ⠿ 材质     7 │ Batch bar (when selected):           │
│  ⠿ 风格     1 │ [3 selected] [Move to..] [Color] [🗑]│
│               │                                      │
└───────────────┴─────────────────────────────────────┘
```

### Left Sidebar (GroupSidebar)

- Fixed items: "All", "Uncategorized" (with counts)
- Divider
- "Groups" header with [+] button to create
- Group list: drag handle (⠿) + name + count
- Right-click group → Rename / Delete
- Drag-and-drop to reorder groups (updates `sort_order`)

### Right Panel (NotionTable)

- Columns: checkbox, Name, Color (swatch), Group, Used (resource count), Actions (⋮)
- Actions dropdown: Edit, Move to Group, Delete
- Click on Name → opens edit modal
- Drag-and-drop rows to reorder within current group view

### Batch Operations Bar

Appears when 1+ tags selected:
- "N selected" label
- "Move to..." dropdown → select target group
- "Color" → color picker, apply to all selected
- "Delete" → confirm modal, then bulk delete

### Tag Form Modal

Used for both create and edit:
- Name (required)
- Name (Chinese) (optional)
- Color picker
- Icon (optional)
- Group dropdown

## File Structure

### Backend

| File | Purpose |
|------|---------|
| `supabase/migrations/099_tag_groups.sql` | New table + alter tags |
| `backend/app/api/admin/tags_router.py` | Admin tags + groups API |
| `backend/app/api/admin/__init__.py` | Register new router |

### Frontend (admin/)

| File | Purpose |
|------|---------|
| `admin/src/pages/tags/index.tsx` | Main page: left-right layout |
| `admin/src/pages/tags/GroupSidebar.tsx` | Left sidebar: group list with DnD |
| `admin/src/pages/tags/TagFormModal.tsx` | Create/edit tag modal |
| `admin/src/api/endpoints/tags.ts` | API client functions |
| `admin/src/App.tsx` | Update route from PlaceholderPage to TagsPage |

## Tech Notes

- Admin app uses Arco Design + NotionTable pattern
- All IDs are Snowflake BIGINT (string in frontend)
- Drag-and-drop: use `@dnd-kit/core` or simple Arco drag (check what admin already uses)
- Backend uses `AdminAuthDep` from `backend/app/core/admin_deps.py`
- API client: `apiClient` from `admin/src/api/client.ts`

## Phase 2 (Future)

- Merge/dedup tags
- Import from Eagle
- AI auto-tagging suggestions
- Tag usage heatmap/analytics
