# Phase 2 & 3: Resource Library + Permission & Sharing

> Date: 2026-02-15
> Status: Approved
> Related: `2026-02-13-team-collaboration-platform-design.md` Phase 2 & 3
> Branch: `feature/Points-capacity-payment-system`
> Supersedes: `2026-02-15-phase2-resource-library-design.md`

---

## 1. Overview

Combined design for Phase 2 (Resource Library) and Phase 3 (Permission & Sharing). Data model designed together, implementation in two batches.

### Phase 2 Scope (Batch 1)
- UUID → BIGINT Snowflake ID migration (all resource-related tables)
- Team Libraries (new entity)
- Personal My Resources UX improvements
- File detail page with preview
- Context menus for files and folders
- Smart Folders with rule engine
- Tags frontend integration
- Remove "All Resources" flat view

### Phase 3 Scope (Batch 2)
- ReBAC permission system (access_overrides + effective role)
- Sharing system (4 share types: link, review, presentation, delivery)
- Share creation UI + external share pages
- Text comments + timecode positioning (simplified review)

### Out of Scope (Deferred)
- Canvas drawing/annotations → Phase 6
- Version comparison → Phase 6
- Plugin system for preview → Future
- Library permissions UI → Phase 3 (but table created in Phase 2)

---

## 2. ID Migration: UUID → BIGINT Snowflake

### 2.1 Why

Current state: `folders`, `resources`, `resource_items`, `resource_versions` use UUID. New tables (`libraries`, `access_overrides`, `shares`) need BIGINT Snowflake. Mixing types creates complexity.

Decision: **Migrate all resource-related tables to BIGINT Snowflake ID** in one migration.

### 2.2 Tables to Migrate

| Table | Current ID Type | Action |
|-------|----------------|--------|
| `folders` | UUID | → BIGINT |
| `resources` | UUID (some already BIGINT via id_generator) | → BIGINT |
| `resource_items` | UUID | → BIGINT |
| `resource_versions` | UUID | → BIGINT |
| `resource_tags` | UUID | → BIGINT |

### 2.3 Migration Strategy

```sql
-- Step 1: Add new BIGINT columns
ALTER TABLE folders ADD COLUMN new_id BIGINT DEFAULT id_generator();
-- ... repeat for all tables

-- Step 2: Populate new IDs for existing rows
UPDATE folders SET new_id = id_generator() WHERE new_id IS NULL;

-- Step 3: Update all foreign key references
-- resource_items.folder_id → map old UUID to new BIGINT
-- resource_items.resource_id → map old UUID to new BIGINT

-- Step 4: Drop old columns, rename new columns
ALTER TABLE folders DROP COLUMN id;
ALTER TABLE folders RENAME COLUMN new_id TO id;
ALTER TABLE folders ADD PRIMARY KEY (id);

-- Step 5: Recreate indexes and constraints
```

### 2.4 Frontend Impact

All service files that reference resource IDs need to handle BIGINT (returned as number from PostgREST). Known pitfall: `useParams()` returns string, use string comparison or normalize.

---

## 3. Data Model

### 3.1 New Table: `libraries`

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
  visibility TEXT NOT NULL DEFAULT 'inherited'
    CHECK (visibility IN ('inherited', 'restricted')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_libraries_scope ON libraries(scope_type, scope_id);
```

### 3.2 New Table: `access_overrides` (Phase 3, created in Phase 2 migration)

```sql
CREATE TABLE access_overrides (
  id          BIGINT PRIMARY KEY DEFAULT id_generator(),
  object_type TEXT NOT NULL CHECK (object_type IN ('library', 'folder', 'project')),
  object_id   BIGINT NOT NULL,
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer', 'none')),
  granted_by  UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (object_type, object_id, user_id)
);
```

### 3.3 New Table: `shares` (Phase 3, created in Phase 2 migration)

```sql
CREATE TABLE shares (
  id              BIGINT PRIMARY KEY DEFAULT id_generator(),
  -- Polymorphic target: exactly one of these should be set
  resource_id     BIGINT REFERENCES resources(id) ON DELETE CASCADE,
  folder_id       BIGINT REFERENCES folders(id) ON DELETE CASCADE,
  library_id      BIGINT REFERENCES libraries(id) ON DELETE CASCADE,
  version_id      BIGINT REFERENCES resource_versions(id) ON DELETE SET NULL,
  share_type      TEXT NOT NULL
    CHECK (share_type IN ('link', 'review', 'presentation', 'delivery')),
  shared_by       UUID NOT NULL REFERENCES auth.users(id),
  share_name      TEXT NOT NULL,
  share_code      VARCHAR(20) UNIQUE NOT NULL,
  password        TEXT,
  allow_download  BOOLEAN NOT NULL DEFAULT true,
  expires_at      TIMESTAMPTZ,
  max_views       INTEGER,
  view_count      INTEGER NOT NULL DEFAULT 0,
  watermark       BOOLEAN NOT NULL DEFAULT false,
  status          TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'expired', 'cancelled')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Check constraint: exactly one target
ALTER TABLE shares ADD CONSTRAINT shares_single_target CHECK (
  (resource_id IS NOT NULL)::int +
  (folder_id IS NOT NULL)::int +
  (library_id IS NOT NULL)::int = 1
);
```

### 3.4 New Table: `share_views` (Phase 3)

```sql
CREATE TABLE share_views (
  id             BIGINT PRIMARY KEY DEFAULT id_generator(),
  share_id       BIGINT NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
  viewer_id      UUID REFERENCES auth.users(id),
  is_favorited   BOOLEAN NOT NULL DEFAULT false,
  last_viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  view_count     INTEGER NOT NULL DEFAULT 1,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (share_id, viewer_id)
);
```

### 3.5 Schema Changes to Existing Tables

```sql
-- folders: add library_id for team libraries
ALTER TABLE folders ADD COLUMN library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

-- resource_items: add library_id for team libraries
ALTER TABLE resource_items ADD COLUMN library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

-- folders: add smart folder support
ALTER TABLE folders ADD COLUMN is_smart BOOLEAN DEFAULT FALSE;
ALTER TABLE folders ADD COLUMN smart_rules JSONB;

-- review_comments: simplified annotation support (Phase 3)
ALTER TABLE review_comments ADD COLUMN share_id BIGINT REFERENCES shares(id) ON DELETE SET NULL;
ALTER TABLE review_comments ADD COLUMN visibility TEXT NOT NULL DEFAULT 'all'
  CHECK (visibility IN ('all', 'team', 'private'));
ALTER TABLE review_comments ADD COLUMN mentions UUID[];
```

### 3.6 Entity Hierarchy

```
Team Workspace:
  Team → Library → Folder → File (resource_item)
                          └→ File (resource_item)

Personal Workspace:
  Personal → My Resources (no library entity) → Folder → File
                                                       └→ File

Permission Inheritance (Phase 3):
  Team Role → Library visibility/override → Folder override → Effective role

Share Targets:
  Share → resource_id (single file)
       → folder_id (entire folder)
       → library_id (entire library)
```

---

## 4. Permission System (Phase 3)

### 4.1 Four Roles

| Role | Capabilities |
|------|-------------|
| admin | view, download, upload, update, copy, move, delete, share, manage |
| editor | view, download, upload, update, copy, move, share |
| viewer | view, download |
| none | no access |

### 4.2 Role Inheritance

```
Team Role (base)
  └── maps to workspace role: owner/admin → admin, member → viewer
       └── Library visibility check
            └── Library override (access_overrides where object_type='library')
                 └── Folder override (access_overrides where object_type='folder')
```

### 4.3 Effective Role Algorithm

```python
def get_effective_role(user_id, object_type, object_id):
    # 1. Check direct override on this object
    override = db.get(access_overrides, object_type, object_id, user_id)
    if override:
        return override.role

    # 2. If object is restricted and no override → no access
    obj = db.get(object_type, object_id)
    if obj.visibility == 'restricted':
        return 'none'

    # 3. Walk up the hierarchy
    if object_type == 'folder' and obj.parent_id:
        return get_effective_role(user_id, 'folder', obj.parent_id)
    if object_type == 'folder' and obj.library_id:
        return get_effective_role(user_id, 'library', obj.library_id)

    # 4. Fall back to team role mapping
    team_role = db.get_team_role(user_id, obj.team_id)
    return {'owner': 'admin', 'admin': 'admin', 'member': 'viewer'}.get(team_role, 'none')
```

### 4.4 Frontend Permission Check

```typescript
const CAPABILITIES: Record<string, string[]> = {
  admin:  ['view','download','upload','update','copy','move','delete','share','manage'],
  editor: ['view','download','upload','update','copy','move','share'],
  viewer: ['view','download'],
  none:   [],
};

function canDo(action: string, role: string): boolean {
  return CAPABILITIES[role]?.includes(action) ?? false;
}
```

---

## 5. Sharing System (Phase 3)

### 5.1 Four Share Types

| Type | Use Case | Features |
|------|----------|----------|
| `link` | Quick sharing | View only, optional password |
| `review` | Video review | Text comments + timecode positioning |
| `presentation` | Client presentation | Browse-only, watermark option |
| `delivery` | Final delivery | Preview + download, expiration |

### 5.2 Share Targets

- **Single file** (`resource_id`) — share one resource
- **Folder** (`folder_id`) — share folder and all contents
- **Library** (`library_id`) — share entire library

### 5.3 External Share Page

Route: `/s/:shareCode`

- Validates share_code, checks password/expiration
- Renders content based on share_type
- Records view in share_views
- Review type: shows comments panel with timecode input

### 5.4 Review Comments (Simplified)

- Text comment + optional timecode (`HH:MM:SS`)
- Visibility: all / team / private
- Mentions: `@user` references
- NO canvas drawing (deferred to Phase 6)

---

## 6. Sidebar Navigation

### 6.1 Personal Workspace

```
Shared
Quick Access
Recycle Bin
─────────────────────────────────
RipVault
My Resources                [+]  ← + creates folder
─────────────────────────────────
Smart Folders               [+]
```

- Remove "All Resources" entry
- Default view: My Resources content

### 6.2 Team Workspace

```
Shared
Quick Access
Recycle Bin
─────────────────────────────────
Team Libraries              [+]  ← + creates new library
  📁 Brand Assets
  📁 Project Footage
  📁 Client Deliverables
─────────────────────────────────
Smart Folders               [+]
```

- Libraries as flat items (contents NOT expanded in tree)
- All folder/file browsing in main content area

---

## 7. Main Content Area

### 7.1 Layout

```
┌──────────────────────────────────────────────────────────┐
│ 📁 Brand Assets                              🔍 Search   │
│ Brand Assets > Design > Logos                共 12 项     │
├──────────────────────────────────────────────────────────┤
│ [Sort ▾]  [Filter]  [Grid|List]      [Upload ▾] [New ▾]  │
├──────────────────────────────────────────────────────────┤
│  📁 Logos    📁 Templates    📹 video.mp4   🖼 img.png    │
└──────────────────────────────────────────────────────────┘
```

### 7.2 Context Menus

**Right-click on File:**
- Open in New Tab
- Share (Phase 3)
- Download Original
- Package Download
- Rename
- Copy To
- Move To
- Move to Recycle Bin (red)

**Right-click on Folder:**
- Open in New Tab
- Share (Phase 3)
- Export Directory
- Rename
- Copy To
- Move To
- Move to Recycle Bin (red)

**Right-click on empty area:**
- Upload File
- Upload Folder
- New Folder
- Refresh

### 7.3 Empty State

Drag-and-drop upload zone: "Click to upload or drag files here"

---

## 8. File Detail Page

### 8.1 Routes

- Personal: `/resources/:resourceId`
- Team: `/t/:teamId/resources/:resourceId`

### 8.2 Layout

```
┌────────────────────────────────────────────────────────────┐
│ ← Back to Brand Assets                              [···]  │
├──────────────────────┬─────────────────────────────────────┤
│                      │  Metadata (name, type, size, etc.)  │
│    Preview Area      │  Tags: [tag1] [tag2] [+]            │
│    (video/audio/     │  Versions: v2, v1                   │
│     image/PDF)       │  Location: Library > Folder          │
│                      │  Comments (Phase 3)                  │
└──────────────────────┴─────────────────────────────────────┘
```

### 8.3 Built-in Preview

| File Type | Method |
|-----------|--------|
| Video | HTML5 `<video>` (reuse VideoPlayer) |
| Audio | HTML5 `<audio>` with waveform |
| Image | `<img>` with zoom/rotate |
| PDF | pdf.js / browser native |
| Other | File icon + metadata + download |

---

## 9. Smart Folders

### 9.1 Data Model

```sql
ALTER TABLE folders ADD COLUMN is_smart BOOLEAN DEFAULT FALSE;
ALTER TABLE folders ADD COLUMN smart_rules JSONB;
```

### 9.2 Rule Format (Eagle-style)

```json
{
  "operator": "AND",
  "match": true,
  "conditions": [
    { "field": "file_type", "op": "eq", "value": "video" },
    { "field": "created_at", "op": "gte", "value": "relative:-7d" }
  ]
}
```

- `operator`: `"AND"` (all) or `"OR"` (any)
- `match`: `true` (match) or `false` (exclude)
- Single level, no nesting

### 9.3 Supported Fields

| Field | Operators |
|-------|----------|
| `filename` | contains, eq, starts_with |
| `file_type` | eq, in |
| `tags` | contains, not_contains |
| `file_size_bytes` | gt, lt, gte, lte |
| `created_at` | gt, lt, gte, lte (ISO or relative) |
| `duration_seconds` | gt, lt, gte, lte |
| `resolution` | eq, contains |
| `source_type` | eq |
| `mime_type` | eq, contains |

### 9.4 Frontend UI

Eagle-style dialog: `[any|all] of the following are [true|false]` + condition rows with [+][-].

---

## 10. Implementation Plan

### Batch 1: Phase 2 (Resource Library)

| Step | Description | Commit |
|------|-------------|--------|
| 1 | UUID → BIGINT migration for resource tables | `refactor: migrate resource tables to BIGINT Snowflake IDs` |
| 2 | New tables: `libraries`, `access_overrides`, `shares`, `share_views` (empty, ready for Phase 3) | `feat: add libraries and Phase 3 table scaffolding` |
| 3 | Backend: Libraries CRUD (repository + service + router) | `feat: add team libraries CRUD` |
| 4 | Backend: Smart Folders query engine | `feat: add smart folder rule engine` |
| 5 | Frontend: Sidebar restructure (remove All Resources, add Team Libraries, Smart Folders) | `refactor: restructure resources sidebar` |
| 6 | Frontend: Main content area (breadcrumb, toolbar, context menus, drag-drop upload) | `feat: add resource content area with context menus` |
| 7 | Frontend: File detail page (preview, metadata, tags, versions) | `feat: add file detail page with preview` |
| 8 | Frontend: Smart Folder UI (rule editor + dynamic results) | `feat: add smart folder creation and filtering` |
| 9 | Frontend: Tags integration in detail page | `feat: integrate tags in file detail page` |

### Batch 2: Phase 3 (Permission & Sharing)

| Step | Description | Commit |
|------|-------------|--------|
| 10 | Backend: ReBAC effective role algorithm + middleware | `feat: add ReBAC permission system` |
| 11 | Backend: Shares CRUD + share_code generation | `feat: add sharing system API` |
| 12 | Frontend: Permission-aware UI (hide/disable actions based on role) | `feat: add permission-based UI controls` |
| 13 | Frontend: Share creation modal (4 types) | `feat: add share creation UI` |
| 14 | Frontend: External share page (`/s/:shareCode`) | `feat: add external share pages` |
| 15 | Frontend: Review comments with timecode | `feat: add review comments with timecode` |
| 16 | Backend + Frontend: Share management (Shared sidebar section) | `feat: add share management view` |
