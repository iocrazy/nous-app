# Team Collaboration Platform — Complete Redesign

> Date: 2026-02-13
> Status: Draft
> Supersedes: `2026-02-12-sidebar-architecture-design.md` (sidebar section)
> Related branches: `feature/sidebar-architecture`, `feature/Points-capacity-payment-system`

---

## 1. Design Goals

Evolve MediaHub from a personal video collection tool to a full team video collaboration platform. Key principles:

- **Unified workspace model** ("个人即团队", inspired by Figma) — every user has a personal workspace, teams are multi-person workspaces
- **Zero-copy philosophy** — sharing and copying resources never duplicate physical files
- **ReBAC permissions** — relationship-based access control with inheritance + override
- **Content deduplication** — same URL parsed by different users shares one physical file

---

## 2. Navigation Architecture

### 2.1 Workspace Switcher (sidebar top)

```
┌─────────────────────────┐
│ (H) heygo's Workspace ▾ │  ← current workspace
├─────────────────────────┤
│   (H) heygo's Workspace │  ← personal (always present)
│   (T) TeamA        Free  │
│   (T) TeamB        Pro   │
│   ─────────────────────  │
│   + Create new           │
└─────────────────────────┘
```

### 2.2 Sidebar Navigation

**Personal Workspace:**

```
[heygo's Workspace ▾]
─────────────────────
Parser                ← personal only
Resources
Projects
Todolist
```

**Team Workspace:**

```
[TeamA ▾]
─────────────────────
Resources
Projects
Todolist
Management            ← team only (members, permissions, roles)
```

### 2.3 Top Bar (right side)

```
[🔍 Search] [📋 Task Center] [🔔 Notifications] [👤 Avatar]
```

| Icon | Function | Interaction |
|------|----------|-------------|
| 🔍 Search | Global search | Cmd+K popup |
| 📋 Task Center | Transfers + task summary | Floating panel |
| 🔔 Notifications | Notification center | Floating panel, unread badge |
| 👤 Avatar | Account menu | Dropdown menu |

### 2.4 Task Center Panel

```
┌──────────────────────────────┐
│  Transfers    Tasks          │  ← two tabs
├──────────────────────────────┤
│  📹 video_abc.mp4    ✅ 100% │  ← download/upload progress
│     52.19 MB           [📂]  │
│  📹 batch_003.mp4   ⏳ 45%  │  ← in progress
│     120 MB          [cancel] │
├──────────────────────────────┤
│  2/3 completed               │
└──────────────────────────────┘
```

- **Transfers tab**: download/upload progress (Parser downloads, project file uploads)
- **Tasks tab**: simplified task reminders (assigned to me + due soon), click to jump to full Todolist

### 2.5 Avatar Dropdown Menu

```
┌──────────────────────┐
│ (avatar) heygo       │
│ heygo@email.com      │
│ [Pro Plan]           │
├──────────────────────┤
│ Profile              │  → Settings/Profile
│ Settings             │  → Settings/General
│ Notifications        │  → notification preferences
├──────────────────────┤
│ Help & Docs          │
│ Feedback             │
├──────────────────────┤
│ Sign out             │
└──────────────────────┘
```

### 2.6 Settings Page (full screen, account-level)

```
Settings
├── Profile             ← name, avatar, email, password
├── Preferences         ← language, theme, download path, notification settings
├── Billing             ← plan management, points balance, purchase, history
├── API Keys            ← API key management
├── AI                  ← AI provider & model configuration
└── Activity Log        ← operation logs
```

Settings is **account-level**, not workspace-level. Always accessed via avatar menu.

---

## 3. Resource Library

### 3.1 Core Tables

#### `resources` — Core resource records

```sql
CREATE TABLE resources (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  creator_id       UUID NOT NULL REFERENCES auth.users(id),
  source_type      VARCHAR(20) NOT NULL CHECK (source_type IN ('web', 'upload')),
  video_id         UUID REFERENCES videos(id) ON DELETE SET NULL,
  filename         VARCHAR(500) NOT NULL,
  file_type        VARCHAR(50),        -- video / image / audio / document
  mime_type        VARCHAR(100),
  file_path        TEXT,                -- physical path (immutable IDs only)
  file_size_bytes  BIGINT,
  duration_seconds INTEGER,
  resolution       VARCHAR(50),
  thumbnail_path   TEXT,
  cover_image_path TEXT,
  current_version  INTEGER NOT NULL DEFAULT 1,
  is_trashed       BOOLEAN NOT NULL DEFAULT false,
  trashed_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `resource_items` — Many-to-many (resource ↔ workspace)

A single resource can appear in multiple workspaces (personal + teams).

```sql
CREATE TABLE resource_items (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id  UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  scope_type   VARCHAR(20) NOT NULL CHECK (scope_type IN ('personal', 'team')),
  scope_id     UUID NOT NULL,         -- user_id or team_id
  folder_id    UUID REFERENCES folders(id) ON DELETE SET NULL,
  added_by     UUID REFERENCES auth.users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `folders` — Virtual folder tree

All folder operations are database-only (zero IO). Moving = updating `folder_id`.

```sql
CREATE TABLE folders (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        VARCHAR(200) NOT NULL,
  parent_id   UUID REFERENCES folders(id) ON DELETE CASCADE,
  scope_type  VARCHAR(20) NOT NULL CHECK (scope_type IN ('personal', 'team')),
  scope_id    UUID NOT NULL,
  created_by  UUID NOT NULL REFERENCES auth.users(id),
  sort_order  INTEGER NOT NULL DEFAULT 0,
  is_system   BOOLEAN NOT NULL DEFAULT false,
  icon        VARCHAR(50),
  color       VARCHAR(20),
  visibility  VARCHAR(20) NOT NULL DEFAULT 'inherited'
    CHECK (visibility IN ('inherited', 'restricted')),
  is_trashed  BOOLEAN NOT NULL DEFAULT false,
  trashed_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `resource_versions` — Version management (upload only)

```sql
CREATE TABLE resource_versions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id      UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  version_number   INTEGER NOT NULL,
  filename         VARCHAR(500),
  file_path        TEXT,
  file_size_bytes  BIGINT,
  mime_type        VARCHAR(100),
  duration_seconds INTEGER,
  resolution       VARCHAR(50),
  thumbnail_path   TEXT,
  uploaded_by      UUID REFERENCES auth.users(id),
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (resource_id, version_number)
);
```

### 3.2 Physical Storage Structure

Paths use **only immutable IDs**. All mutable info (names, folders, tags) is database-only.

```
MediaHub.library/
├── resources/
│   ├── web/{platform}/{external_id}/        ← Parser shared pool (dedup'd)
│   ├── users/{user_id}/upload/2026-02/      ← personal uploads (versioned)
│   │   └── {resource_id}/
│   └── teams/{team_id}/2026-02/             ← team uploads (versioned)
│       └── {resource_id}/
├── projects/{team_id}/{project_id}/
│   └── files/2026-02/
│       └── {file_id}/
├── cache/
├── temp/
└── exports/
```

### 3.3 Content Deduplication

Dedup key: `videos.platform_id`

```
User parses URL → extract platform_id
Step 1: SELECT id, file_path FROM videos WHERE platform_id = ? LIMIT 1 (service role)
Step 2:
  Not found → download + write videos + write resources
  Found → check resources WHERE creator_id = current_user AND video_id = found_id
    Exists → return "already downloaded" (no new record)
    Not exists → write resources record reusing same file_path (zero-copy)
```

- Same user re-parsing → "already downloaded" toast, no new record
- Different user parsing same URL → new resources record, same physical file
- Physical file deletion by reference counting (when last resource pointing to file_path is deleted)

### 3.4 Zero-Copy Operations

| Operation | What happens | Physical IO |
|-----------|-------------|-------------|
| **Share** (same resource) | New `resource_items` row, same `resource_id` | None |
| **Copy** (to another workspace) | New `resources` row, same `file_path` | None |
| **Move** (within workspace) | Update `folder_id` | None |
| **Rename** | Update `filename` in DB | None |
| **Delete** | Set `is_trashed = true` | None |

---

## 4. Permission System (ReBAC)

### 4.1 Model: Inheritance + Override

```
Team role (base)
  └── inherits to all folders/projects
       └── override only for exceptions (restrict or elevate)
```

### 4.2 Four Roles

| Role | Capabilities |
|------|-------------|
| admin | view, download, upload, update, copy, move, delete, share, manage |
| editor | view, download, upload, update, copy, move, share |
| viewer | view, download |
| none | no access |

### 4.3 Role Mapping from Team Role

| Team Role | Default Workspace Role |
|-----------|----------------------|
| owner | admin |
| admin | admin |
| member | viewer |

### 4.4 Override Table

```sql
CREATE TABLE access_overrides (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  object_type VARCHAR(20) NOT NULL CHECK (object_type IN ('folder', 'project')),
  object_id   UUID NOT NULL,
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role        VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'editor', 'viewer', 'none')),
  granted_by  UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (object_type, object_id, user_id)
);
```

### 4.5 Effective Role Algorithm

```python
def get_effective_role(user_id, object_type, object_id):
    # 1. Check direct override
    override = db.query(access_overrides,
        object_type=object_type, object_id=object_id, user_id=user_id)
    if override:
        return override.role

    # 2. If object is restricted and no override → no access
    obj = db.get(object_type, object_id)
    if obj.visibility == 'restricted':
        return 'none'

    # 3. Inherit from parent folder
    if object_type == 'folder' and obj.parent_id:
        return get_effective_role(user_id, 'folder', obj.parent_id)

    # 4. Fall back to team role
    team_role = db.get_team_role(user_id, obj.team_id)
    return {'owner': 'admin', 'admin': 'admin', 'member': 'viewer'}.get(team_role, 'none')
```

### 4.6 Frontend Permission Check

```typescript
function canDo(action: string, role: string): boolean {
  const capabilities: Record<string, string[]> = {
    admin:  ['view','download','upload','update','copy','move','delete','share','manage'],
    editor: ['view','download','upload','update','copy','move','share'],
    viewer: ['view','download'],
    none:   [],
  };
  return capabilities[role]?.includes(action) ?? false;
}
```

---

## 5. Sharing System

### 5.1 Four Share Types

| Type | Use Case | Features |
|------|----------|----------|
| `link` | Quick sharing | View only, optional password |
| `review` | Frame-accurate review | Timecoded annotations, drawing, version comparison |
| `presentation` | Client presentation | Browse-only, watermark option |
| `delivery` | Final delivery | Preview + download, expiration |

### 5.2 Tables

#### `shares`

```sql
CREATE TABLE shares (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id     UUID REFERENCES resources(id) ON DELETE CASCADE,
  project_file_id UUID REFERENCES project_files(id) ON DELETE CASCADE,
  folder_id       UUID REFERENCES folders(id) ON DELETE CASCADE,
  version_id      UUID REFERENCES file_versions(id) ON DELETE SET NULL,
  share_type      VARCHAR(20) NOT NULL
    CHECK (share_type IN ('link', 'review', 'presentation', 'delivery')),
  shared_by       UUID NOT NULL REFERENCES auth.users(id),
  share_name      VARCHAR(200) NOT NULL,
  share_code      VARCHAR(20) UNIQUE NOT NULL,
  password        TEXT,
  allow_download  BOOLEAN NOT NULL DEFAULT true,
  expires_at      TIMESTAMPTZ,
  max_views       INTEGER,
  view_count      INTEGER NOT NULL DEFAULT 0,
  watermark       BOOLEAN NOT NULL DEFAULT false,
  status          VARCHAR(20) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'expired', 'cancelled')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `share_views`

```sql
CREATE TABLE share_views (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  share_id      UUID NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
  viewer_id     UUID REFERENCES auth.users(id),
  is_favorited  BOOLEAN NOT NULL DEFAULT false,
  last_viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  view_count    INTEGER NOT NULL DEFAULT 1,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (share_id, viewer_id)
);
```

### 5.3 Video Annotations (Review Shares)

Extend existing `review_comments` table:

```sql
ALTER TABLE review_comments
  ADD COLUMN drawing_data JSONB,           -- canvas drawing (Fabric.js/Konva.js)
  ADD COLUMN visibility VARCHAR(20) NOT NULL DEFAULT 'all'
    CHECK (visibility IN ('all', 'team', 'private')),
  ADD COLUMN attachments TEXT[],           -- attachment file paths
  ADD COLUMN mentions UUID[],             -- mentioned user IDs
  ADD COLUMN share_id UUID REFERENCES shares(id) ON DELETE SET NULL;
```

Annotation system is **frontend HTML5 Canvas** (Fabric.js or Konva.js) overlaid on `<video>`. Drawing data stored as JSONB. Each annotation is tied to a version + timecode.

---

## 6. Project Workflow

### 6.1 Workflow Tables

#### `project_workflows`

```sql
CREATE TABLE project_workflows (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id    UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  name       VARCHAR(100) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `workflow_nodes`

```sql
CREATE TABLE workflow_nodes (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES project_workflows(id) ON DELETE CASCADE,
  name        VARCHAR(100) NOT NULL,
  status_type VARCHAR(20) NOT NULL
    CHECK (status_type IN ('not_started', 'in_progress', 'completed')),
  node_type   VARCHAR(30) NOT NULL DEFAULT 'status'
    CHECK (node_type IN ('status', 'milestone', 'gate')),
  sort_order  INTEGER NOT NULL,
  color       VARCHAR(20),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 6.2 Project Extensions

```sql
ALTER TABLE projects
  ADD COLUMN visibility VARCHAR(20) NOT NULL DEFAULT 'inherited'
    CHECK (visibility IN ('inherited', 'restricted')),
  ADD COLUMN workflow_id UUID REFERENCES project_workflows(id);
```

### 6.3 Project Members

```sql
CREATE TABLE project_members (
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role       VARCHAR(20) NOT NULL DEFAULT 'viewer'
    CHECK (role IN ('manager', 'editor', 'viewer', 'external')),
  invited_by UUID REFERENCES auth.users(id),
  joined_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (project_id, user_id)
);
```

### 6.4 Tasks

#### `project_tasks`

```sql
CREATE TABLE project_tasks (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_node_id  UUID REFERENCES workflow_nodes(id) ON DELETE SET NULL,
  title            VARCHAR(500) NOT NULL,
  description      TEXT,
  task_type        VARCHAR(30) NOT NULL DEFAULT 'general'
    CHECK (task_type IN ('general', 'storyboard', 'script', 'filming', 'editing', 'review')),
  assignee_id      UUID REFERENCES auth.users(id),
  due_date         DATE,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  status           VARCHAR(20) NOT NULL DEFAULT 'todo'
    CHECK (status IN ('todo', 'in_progress', 'done', 'cancelled', 'on_hold')),
  created_by       UUID REFERENCES auth.users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `task_assets` — Link tasks to resources/files

```sql
CREATE TABLE task_assets (
  task_id     UUID NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  resource_id UUID REFERENCES resources(id) ON DELETE CASCADE,
  file_id     UUID REFERENCES project_files(id) ON DELETE CASCADE,
  added_by    UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 6.5 Project ↔ Resource Relationship

Bidirectional flow, all zero-copy:

| Direction | Operation | Implementation |
|-----------|-----------|---------------|
| Resources → Project | "Add to Project" | New `project_files` row, `file_path` = resource's `file_path` |
| Resources → Project | "Copy to Project" | New `project_files` row, same `file_path` (zero IO) |
| Project → Resources | "Save to Resources" | New `resources` row, same `file_path` (zero IO) |

---

## 7. Billing & Plans

### 7.1 Team Plans

```sql
CREATE TABLE team_plans (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id             UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  plan_tier           VARCHAR(30) NOT NULL DEFAULT 'free'
    CHECK (plan_tier IN ('free', 'studio_standard', 'studio_pro',
                          'enterprise_standard', 'enterprise_pro', 'enterprise_flagship')),
  max_seats           INTEGER NOT NULL DEFAULT 1,
  max_storage_bytes   BIGINT NOT NULL DEFAULT 5368709120,  -- 5GB
  max_project_members INTEGER NOT NULL DEFAULT 5,
  custom_permissions  BOOLEAN NOT NULL DEFAULT false,
  custom_workflows    BOOLEAN NOT NULL DEFAULT false,
  expires_at          TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 7.2 Seat-Based Billing

- Plans sold per-seat (席位)
- Team admin manages seats in Settings → Billing
- Points system for consumable features (AI analysis, extra storage)
- Payment: WeChat Pay / Alipay (via XunHuPay or direct API)

---

## 8. Smart Folders

Custom user-defined filter rules (like Notion filter views), not preset/locked.

### 8.1 Extend Existing Table

```sql
ALTER TABLE smart_collections
  ADD COLUMN scope_type VARCHAR(20) DEFAULT 'personal'
    CHECK (scope_type IN ('personal', 'team')),
  ADD COLUMN scope_id UUID;
```

### 8.2 Custom Filter Rules

```typescript
interface SmartFolderRules {
  match: 'all' | 'any';                    // AND / OR
  conditions: {
    field: string;                          // filterable field
    operator: string;                       // comparison operator
    value: string | number | string[];      // filter value
    unit?: string;                          // 'MB' | 'days' | 'px' etc.
  }[];
}
```

### 8.3 Available Filter Fields

| Field | Operators | Value Type |
|-------|-----------|------------|
| file_type | eq, neq | video, image, audio, document |
| file_size | gt, lt, between | number + unit (MB) |
| created_at | within, before, after, between | date / relative days |
| source | eq | web, upload |
| platform | eq, in | douyin, youtube, bilibili, twitter |
| tags | has, has_any, has_none | tag selection |
| author | eq, contains | text |
| resolution | eq, gt, lt | 720p, 1080p, 4K |
| duration | gt, lt, between | seconds |
| title | contains, starts_with | text |

**Example — "Recent Large Videos":**
```json
{
  "match": "all",
  "conditions": [
    { "field": "file_type", "operator": "eq", "value": "video" },
    { "field": "file_size", "operator": "gt", "value": 100, "unit": "MB" },
    { "field": "created_at", "operator": "within", "value": 30, "unit": "days" }
  ]
}
```

Names are fully user-customizable.

---

## 9. Recycle Bin

### 9.1 Unified Recycle Bin

One recycle bin per workspace. All delete operations go to the same bin.

### 9.2 Implementation

No new table. Reuse existing `is_trashed` + `trashed_at` fields:

| Table | Fields | Notes |
|-------|--------|-------|
| `resources` | `is_trashed`, `trashed_at` | already exists |
| `project_files` | `is_trashed`, `trashed_at` | already exists |
| `folders` | `is_trashed`, `trashed_at` | add to folders table |

### 9.3 Rules

- Delete → set `is_trashed = true` (no physical deletion)
- **30-day auto-cleanup**: cron job physically deletes files + DB records
- Restore → set `is_trashed = false`, return to original folder (or root if folder deleted)
- Empty recycle bin → immediate physical deletion

### 9.4 Query

```sql
SELECT 'resource' AS item_type, r.id, r.filename, r.trashed_at
FROM resources r
JOIN resource_items ri ON ri.resource_id = r.id
WHERE r.is_trashed = true AND ri.scope_type = ? AND ri.scope_id = ?

UNION ALL

SELECT 'file', pf.id, pf.filename, pf.trashed_at
FROM project_files pf
WHERE pf.is_trashed = true
  AND pf.project_id IN (SELECT id FROM projects WHERE team_id = ?)

ORDER BY trashed_at DESC;
```

---

## 10. Tags System

### 10.1 Extend Existing Tags Table

```sql
ALTER TABLE tags
  ADD COLUMN scope_type VARCHAR(20) DEFAULT 'personal'
    CHECK (scope_type IN ('personal', 'team')),
  ADD COLUMN scope_id UUID;
```

### 10.2 Tag Scopes

| Type | scope_type | Visibility |
|------|-----------|------------|
| System tags | `system` (unchanged) | Global |
| Personal tags | `personal` + user_id | Self only |
| Team tags | `team` + team_id | All team members |

### 10.3 Resource-Tag Binding

```sql
CREATE TABLE resource_tags (
  resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  tag_id      UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  tagged_by   UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (resource_id, tag_id)
);
```

### 10.4 Management

- Personal tags: create/delete in Resources view
- Team tags: Management → Tags page (admin permission required)
- Smart folder `tags` filter condition JOINs `resource_tags`

---

## 11. Complete New Table Summary

### New Tables (13)

| # | Table | Purpose |
|---|-------|---------|
| 1 | `resources` | Core resource records |
| 2 | `resource_items` | Resource ↔ workspace mapping |
| 3 | `folders` | Virtual folder tree |
| 4 | `resource_versions` | Upload version history |
| 5 | `shares` | Unified sharing (4 types) |
| 6 | `share_views` | View/favorite tracking |
| 7 | `project_workflows` | Workflow templates |
| 8 | `workflow_nodes` | Workflow stage nodes |
| 9 | `project_members` | Project-level roles |
| 10 | `access_overrides` | ReBAC permission overrides |
| 11 | `team_plans` | Seat-based billing |
| 12 | `project_tasks` | Tasks within projects |
| 13 | `task_assets` | Task-to-asset linking |
| 14 | `resource_tags` | Resource-tag binding |

### Modified Tables

| Table | Changes |
|-------|---------|
| `review_comments` | +drawing_data, +visibility, +attachments, +mentions, +share_id |
| `projects` | +visibility, +workflow_id |
| `smart_collections` | +scope_type, +scope_id |
| `tags` | +scope_type, +scope_id |
| `folders` | +is_trashed, +trashed_at |

### Unchanged Tables (leveraged as-is)

- `videos` — dedup key via `platform_id`, storage pool
- `project_files` — existing is_trashed/trashed_at already present
- `file_versions` — existing version management for project files
- `teams` / `team_members` — existing team structure
- `point_packages` / `point_transactions` / `payment_orders` — existing points system

---

## 12. Implementation Phases

### Phase 1: Foundation
- Workspace switcher component (replaces TeamSwitcher)
- `folders` + `resources` + `resource_items` tables
- Basic Resources view with folder tree
- Navigation restructure (sidebar + top bar)

### Phase 2: Resource Management
- Upload + version management (`resource_versions`)
- Content dedup for Parser downloads
- `resource_tags` + tag scope extension
- Smart folders with custom filter rules
- Recycle bin (unified)

### Phase 3: Permission & Sharing
- ReBAC: `access_overrides` + effective role algorithm
- `shares` + `share_views` tables
- Share creation UI (4 types)
- External share pages (review, presentation, delivery)

### Phase 4: Project Workflow
- `project_workflows` + `workflow_nodes`
- `project_tasks` + `task_assets`
- `project_members` with project-level roles
- Kanban / Table / Timeline views
- Project ↔ Resource bidirectional flow

### Phase 5: Billing & Plans
- `team_plans` + seat management
- Plan tier gating in UI
- Settings page (Profile, Preferences, Billing, API, AI, Logs)
- WeChat Pay / Alipay integration

### Phase 6: Review & Annotations
- Video annotation canvas (Fabric.js / Konva.js)
- `review_comments` extensions (drawing_data, visibility, mentions)
- Review share workflow
- Version comparison
