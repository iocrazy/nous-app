# Phase 2: Resource Library — Libraries + File Management + Smart Folders

> Date: 2026-02-15
> Status: Approved
> Related: `2026-02-13-team-collaboration-platform-design.md` Phase 2
> Branch: `feature/Points-capacity-payment-system`

---

## 1. Overview

Evolve the Resources view from a flat "All Resources" display into a structured library system with team libraries, file detail pages, context menus, and smart folders. Reference implementations: MediaTrack, Eagle, Pixcall.

### What's Already Done (Phase 2 Storage)
- Content dedup for parser downloads (zero-copy cross-user)
- Download path restructuring (`resources/web/{platform}/{id}/`)
- Auto-create resource records after Celery download
- Backfill migration for existing downloads
- Garbage collection mechanism (orphan trigger + 30-day cleanup)

### What This Design Covers
- Team Libraries (new entity)
- Personal My Resources UX improvements
- File detail page with preview
- Context menus for files and folders
- Smart Folders with rule engine
- Tags frontend integration
- Remove "All Resources" flat view

### Out of Scope (Deferred)
- Library-level permissions → Phase 3 (ReBAC)
- Share functionality → Phase 3 (placeholder in context menu)
- Plugin system for file preview → Future

---

## 2. Data Model

### 2.1 New Table: `libraries`

```sql
CREATE TABLE libraries (
  id         BIGINT PRIMARY KEY DEFAULT id_generator(),
  name       TEXT NOT NULL,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('team')),
  scope_id   TEXT NOT NULL,          -- team_id
  created_by UUID NOT NULL REFERENCES auth.users(id),
  icon       TEXT,                    -- emoji or icon name
  color      TEXT,                    -- hex color
  sort_order INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_libraries_scope ON libraries(scope_type, scope_id);
```

**Key constraint**: `scope_type` is always `'team'`. Personal workspace does not use libraries — "My Resources" acts as a single implicit library.

### 2.2 Schema Changes to Existing Tables

```sql
-- folders: add library_id for team libraries
ALTER TABLE folders ADD COLUMN library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

-- resource_items: add library_id for team libraries
ALTER TABLE resource_items ADD COLUMN library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

-- folders: add smart folder support
ALTER TABLE folders ADD COLUMN is_smart BOOLEAN DEFAULT FALSE;
ALTER TABLE folders ADD COLUMN smart_rules JSONB;
```

### 2.3 Hierarchy

```
Team Workspace:
  Team → Library → Folder → File (resource_item)
                          └→ File (resource_item)

Personal Workspace:
  Personal → My Resources (no library entity) → Folder → File
                                                       └→ File
```

- Team: `library_id` required on folders and resource_items
- Personal: `library_id = NULL`, uses existing scope_type/scope_id model

---

## 3. Sidebar Navigation

### 3.1 Personal Workspace

```
Shared                          ← existing, keep
Quick Access                    ← existing, keep
Recycle Bin                     ← existing, keep
─────────────────────────────────
RipVault                        ← parser downloads
My Resources                [+] ← click shows content, + creates folder
─────────────────────────────────
Smart Folders               [+] ← smart folder rules
```

- Remove "All Resources" entry
- Default view on entering Resources: My Resources content

### 3.2 Team Workspace

```
Shared
Quick Access
Recycle Bin
─────────────────────────────────
Team Libraries              [+] ← + creates new library
  📁 Brand Assets               ← click → main area shows library content
  📁 Project Footage
  📁 Client Deliverables
─────────────────────────────────
Smart Folders               [+]
```

- Libraries listed as flat items under "Team Libraries" header
- Library contents (folders/files) do NOT expand in sidebar tree
- All folder/file browsing happens in the main content area

---

## 4. Main Content Area

### 4.1 Layout

```
┌──────────────────────────────────────────────────────────┐
│ 📁 Brand Assets                              🔍 Search   │ ← library/section name + search
│ Brand Assets > Design > Logos                共 12 项     │ ← breadcrumb + count
├──────────────────────────────────────────────────────────┤
│ [Sort ▾]  [Filter]  [Grid|List]      [Upload ▾] [New ▾]  │ ← toolbar
├──────────────────────────────────────────────────────────┤
│                                                          │
│  📁 Logos    📁 Templates    📹 video.mp4   🖼 img.png    │ ← folders + files mixed
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 4.2 Toolbar

| Button | Function |
|--------|----------|
| Sort | By name / date / size / type |
| Filter | By file type (video/image/audio/document) |
| View Toggle | Grid / List |
| Upload | Upload File / Upload Folder |
| New | New Folder |

### 4.3 Breadcrumb Navigation

`Library Name > Folder A > Subfolder B`

Click any breadcrumb segment to navigate back to that level.

### 4.4 Empty State

Display drag-and-drop upload zone with "Click to upload or drag files here" placeholder card (reference: MediaTrack).

### 4.5 Context Menus

**Right-click on File:**

| Action | Description |
|--------|------------|
| Open in New Tab | Open file detail page in new browser tab |
| Share | Placeholder → Phase 3 |
| Download Original | Download the file |
| Package Download | Zip download (beta) |
| Rename | Inline rename |
| Copy To | Copy resource_item to another library/folder |
| Move To | Move resource_item to another library/folder |
| Move to Recycle Bin | Soft delete (red text) |

**Right-click on Folder:**

| Action | Description |
|--------|------------|
| Open in New Tab | Open folder in new browser tab |
| Share | Placeholder → Phase 3 |
| Export Directory | Export folder structure as text/JSON |
| Rename | Inline rename |
| Copy To | Copy folder to another location |
| Move To | Move folder to another location |
| Move to Recycle Bin | Soft delete (red text) |

**Right-click on empty area (inside library/folder):**

| Action | Description |
|--------|------------|
| Upload File | File picker |
| Upload Folder | Folder picker |
| New Folder | Create subfolder |
| Refresh | Reload content |

---

## 5. File Detail Page

### 5.1 Route

- Personal: `/resources/:resourceId`
- Team: `/t/:teamId/resources/:resourceId`

### 5.2 Layout

```
┌────────────────────────────────────────────────────────────┐
│ ← Back to Brand Assets                              [···]  │
├──────────────────────┬─────────────────────────────────────┤
│                      │  Filename: interview_final.mp4      │
│                      │  Type: Video · MP4                  │
│    ┌────────────┐    │  Size: 128.5 MB                     │
│    │            │    │  Duration: 3:00                      │
│    │  Preview   │    │  Resolution: 1920x1080               │
│    │   Area     │    │  Created: Feb 15, 2026               │
│    │            │    │  Creator: heygo                       │
│    └────────────┘    │─────────────────────────────────────│
│                      │  Tags: [interview] [final] [+]       │
│                      │─────────────────────────────────────│
│                      │  Versions                            │
│                      │  v2 — Feb 15 — interview_final.mp4   │
│                      │  v1 — Feb 13 — interview_draft.mp4   │
│                      │─────────────────────────────────────│
│                      │  Location                            │
│                      │  Brand Assets > Interviews            │
└──────────────────────┴─────────────────────────────────────┘
```

### 5.3 Preview Capabilities (Built-in)

| File Type | Preview Method |
|-----------|---------------|
| Video | HTML5 `<video>` player (reuse existing VideoPlayer) |
| Audio | HTML5 `<audio>` player with waveform visualization |
| Image | `<img>` with zoom/rotate controls |
| PDF | pdf.js or browser native |
| Other | File icon + metadata + download button |

### 5.4 Info Panel

- **Metadata**: filename, type, size, duration, resolution, created date, creator
- **Tags**: add/remove tags (connected to existing resource_tags system)
- **Versions**: list all versions, click to switch preview, upload new version
- **Location**: breadcrumb showing library > folder path, clickable

### 5.5 More Actions Menu [···]

Same actions as file context menu: Download, Share, Rename, Copy To, Move To, Delete.

---

## 6. Smart Folders

### 6.1 Data Model

Reuse `folders` table with new fields:

```sql
ALTER TABLE folders ADD COLUMN is_smart BOOLEAN DEFAULT FALSE;
ALTER TABLE folders ADD COLUMN smart_rules JSONB;
```

### 6.2 Rule Format

```json
{
  "operator": "AND",
  "match": true,
  "conditions": [
    { "field": "file_type", "op": "eq", "value": "video" },
    { "field": "created_at", "op": "gte", "value": "relative:-7d" },
    { "field": "file_size_bytes", "op": "gt", "value": 10485760 }
  ]
}
```

- `operator`: `"AND"` (all conditions) or `"OR"` (any condition)
- `match`: `true` (match) or `false` (exclude) — Eagle-style "are true/false"
- `conditions`: flat list, no nesting

### 6.3 Supported Filter Fields

| Field | Operators | Value Type |
|-------|----------|------------|
| `filename` | contains, eq, starts_with | string |
| `file_type` | eq, in | video/image/audio/document |
| `tags` | contains, not_contains | tag name |
| `file_size_bytes` | gt, lt, gte, lte | number (bytes) |
| `created_at` | gt, lt, gte, lte | ISO date or relative (`-7d`, `-30d`) |
| `duration_seconds` | gt, lt, gte, lte | number (seconds) |
| `resolution` | eq, contains | string (e.g., "1920x1080") |
| `source_type` | eq | web/upload |
| `mime_type` | eq, contains | string |

### 6.4 Frontend UI

Click [+] next to Smart Folders → modal dialog:

```
┌─────────────────────────────────────────┐
│  New Smart Folder                    ✕  │
│                                         │
│  Name: [________________________]       │
│                                         │
│  [any ▾] of the following are [true ▾]  │
│                                         │
│  [file_type ▾] [equals ▾] [video ▾] [-] │
│  [created_at ▾] [after ▾] [7 days] [-]  │
│                                    [+]  │
│                                         │
│              [Cancel]  [Create]         │
└─────────────────────────────────────────┘
```

### 6.5 Backend Query

When user clicks a Smart Folder, backend dynamically builds a Supabase query from `smart_rules` JSON and returns matching resources within the user's scope.

---

## 7. Implementation Plan (High-Level)

### Step 1: Database Migration
- New `libraries` table
- Add `library_id` to `folders` and `resource_items`
- Add `is_smart` + `smart_rules` to `folders`

### Step 2: Backend — Libraries CRUD
- Repository: `LibrariesRepository`
- Service: `LibrariesService`
- Router: libraries endpoints under `/resources/libraries/`

### Step 3: Backend — Smart Folders
- Smart folder CRUD (reuse folders endpoints)
- Dynamic query builder for smart_rules
- API endpoint to fetch smart folder results

### Step 4: Frontend — Sidebar Restructure
- Remove All Resources
- Add Team Libraries section (team workspace)
- Add library CRUD UI
- Smart Folders section with [+] and rule editor

### Step 5: Frontend — Main Content Area
- Breadcrumb navigation component
- Toolbar (sort/filter/view toggle/upload/new)
- Context menus (file, folder, empty area)
- Drag-and-drop upload zone
- Empty state

### Step 6: Frontend — File Detail Page
- Route setup
- Preview components (video/audio/image/PDF)
- Info panel (metadata, tags, versions, location)
- More actions menu

### Step 7: Frontend — Tags Integration
- Tag management in detail page
- Tag filter in Smart Folders

---

## 8. Migration from Current State

| Current | After |
|---------|-------|
| All Resources (flat grid) | **Removed** |
| Shared | **Keep** (top of sidebar) |
| Quick Access | **Keep** (top of sidebar) |
| Recycle Bin | **Keep** (top of sidebar) |
| RipVault | **Keep** (below separator) |
| My Resources + folder tree | **Keep** — single implicit library, right-click context menu |
| Smart Folders (placeholder) | **Implement** — rule engine + query builder |
| Team resources (flat) | **Replace** with Team Libraries |
