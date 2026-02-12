# Sidebar Architecture Design — Team Collaboration Upgrade

> Date: 2026-02-12
> Status: Approved
> Related branches: `feature/mediatrack-phase2`, `feature/admin-web-design`

---

## 1. Design Goals

MediaHub is upgrading from a personal video collection tool to a team video collaboration platform. The sidebar must:

- Support personal mode (free users) and team mode (paid users)
- Dynamically show/hide navigation items based on role permissions
- Provide immersive project-level navigation when entering a project
- Keep personal tools (Parser, My Library) always accessible
- Cleanly separate personal and team content (remove current Team Library tab)

---

## 2. Sidebar State Machine

The sidebar operates in three states:

```
┌──────────────┐   select team   ┌──────────────┐   enter project  ┌──────────────┐
│   Personal   │ ─────────────→  │     Team     │ ──────────────→  │   Project    │
│    Mode      │ ←─────────────  │     Mode     │ ←──────────────  │    Mode      │
└──────────────┘  switch to      └──────────────┘   ← Projects     └──────────────┘
                  Personal                          back button
```

### 2.1 Personal Mode

No team selected, or user selected "Personal" in TeamSwitcher.

```
[MediaHub Logo]
[Personal ▾]              ← TeamSwitcher component
─────────────────────────
Parser                    ← always visible
My Library                ← always visible
  ▸ Smart Collections
  ▸ Storage Cleanup
Dashboard                 ← always visible
─────────────────────────
Settings                  ← always visible
```

### 2.2 Team Mode

User selected a team. Navigation items vary by role permissions.

```
[MediaHub Logo]
[Team A ▾]                ← TeamSwitcher shows current team
─────────────────────────
Projects                  ← project.view
  ▸ Brand Video
  ▸ Product Demo
  + New Project           ← project.create (admin/editor)
Resources                 ← resource.view
Members                   ← member.view (admin/owner)
Billing                   ← billing.view (owner only)
─────────────────────────
Parser
My Library
─────────────────────────
Settings
```

### 2.3 Project Mode (Immersive)

User clicked into a specific project. Sidebar switches to project-internal navigation with a back button.

```
[← Projects]  Team A      ← back to team mode
[Project A]               ← project name
─────────────────────────
FILES
  ▸ Intro/
  ▸ Main/
  ▸ Outro/
  + Upload                ← resource.upload (editor+)
REVIEW
  Annotations             ← review.view
  Approvals               ← review.approve (reviewer+)
─────────────────────────
Members                   ← project member list
Activity                  ← project timeline
Settings                  ← project.manage (admin+)
```

---

## 3. Permission System

### 3.1 Permission Keys

```typescript
type Permission =
  | 'project.view'
  | 'project.create'
  | 'project.manage'
  | 'resource.view'
  | 'resource.upload'
  | 'member.view'
  | 'member.manage'
  | 'billing.view'
  | 'review.view'
  | 'review.approve';
```

### 3.2 Role Presets

| Role | Permissions |
|------|------------|
| owner | `*` (all) |
| admin | `project.*`, `resource.*`, `member.*`, `review.*` |
| editor | `project.view`, `project.create`, `resource.*`, `review.view` |
| reviewer | `project.view`, `resource.view`, `review.*` |
| viewer | `project.view`, `resource.view` |

### 3.3 Sidebar Item Visibility

| Nav Item | Required Permission | Visible To |
|----------|-------------------|------------|
| Projects | `project.view` | all team members |
| + New Project | `project.create` | owner / admin / editor |
| Resources | `resource.view` | all team members |
| + Upload | `resource.upload` | owner / admin / editor |
| Members | `member.view` | owner / admin |
| Billing | `billing.view` | owner only |
| **Inside Project:** | | |
| Files + Upload | `resource.upload` | owner / admin / editor |
| Review / Approvals | `review.view` | all members |
| Approve button | `review.approve` | owner / admin / reviewer |
| Project Settings | `project.manage` | owner / admin |

### 3.4 API Contract

```typescript
// GET /api/v1/teams/current/permissions
// Returns current user's permissions in the active team
interface TeamPermissions {
  team_id: string;
  role: string;
  permissions: string[];  // e.g. ['project.view', 'resource.*']
}
```

Frontend uses a helper to check permissions:

```typescript
function hasPermission(perms: string[], required: string): boolean {
  return perms.some(p =>
    p === '*' ||
    p === required ||
    (p.endsWith('.*') && required.startsWith(p.slice(0, -1)))
  );
}

// Usage in sidebar
{hasPermission(perms, 'member.view') && <SidebarItem icon={Users} label="Members" />}
```

---

## 4. Library Optimization

### What Changes

| Current | New | Reason |
|---------|-----|--------|
| Library with [My Library] / [Team Library] tabs | My Library only (personal) | Team content moves to sidebar → Resources |
| Team selector modal inside Library | TeamSwitcher at sidebar top | More prominent, consistent context |
| Team Collections in Library | Resources section in team mode | Upgraded to folders + collections + shared assets |

### What Gets Removed

- `LibraryTabs` component (the My Library / Team Library tab switcher)
- `TeamLibraryView` component
- `activeLibraryTab` state in App.tsx
- `selectedTeamId` in library preferences localStorage

### What Gets Added

- `TeamSwitcher` component (sidebar top, dropdown)
- `ResourcesView` component (team resources with folder tree)
- `activeTeamId` global state (replaces `selectedTeamId`)

---

## 5. Pricing Model

### Tiers

| Tier | Price | Features |
|------|-------|----------|
| Free | ¥0 | Parser + My Library + 5GB storage |
| Pro | ¥29/user/month | + AI analysis + 50GB storage |
| Team | ¥59/user/month | + Projects + Review + Version management + 200GB storage |

### Payment

- Domestic payment: WeChat Pay / Alipay
- Integration via XunHuPay (虎皮椒) or direct WeChat Pay API
- Per-seat billing: team admin manages seats in Billing page

### Sidebar Gating

Free users see only personal mode items. Team items show a lock/upgrade prompt if the team hasn't subscribed.

---

## 6. Key Components

### 6.1 TeamSwitcher

Location: sidebar top, below logo.

```
┌─────────────────────────┐
│ [Avatar] Personal    ▾  │  ← current context
├─────────────────────────┤
│   ✓ Personal            │  ← always present
│     Team A              │
│     Team B              │
│   ──────────────────    │
│   + Create Team         │
│   ↗ Join Team           │
└─────────────────────────┘
```

State: `activeTeamId: string | null` (null = personal mode)

### 6.2 Sidebar Container

```typescript
interface SidebarProps {
  mode: 'personal' | 'team' | 'project';
  activeTeamId: string | null;
  activeProjectId: string | null;
  permissions: string[];
}
```

Renders different navigation items based on mode + permissions.

### 6.3 ProjectSidebar

The immersive project sidebar shown in project mode. Contains:
- Back button (returns to team mode)
- Project name header
- File folder tree (collapsible)
- Upload button
- Review section
- Members / Activity / Settings

---

## 7. State Management

```typescript
// App.tsx top-level state additions
const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
const [teamPermissions, setTeamPermissions] = useState<string[]>([]);
const [sidebarMode, setSidebarMode] = useState<'personal' | 'team' | 'project'>('personal');

// Derived
const currentTeam = teams.find(t => t.id === activeTeamId);

// Effects
// When activeTeamId changes → fetch permissions, set mode to 'team'
// When activeProjectId changes → set mode to 'project'
// When back button clicked → clear activeProjectId, set mode to 'team'
// When Personal selected → clear both, set mode to 'personal'
```

---

## 8. Branch Coordination

### Ownership

| Area | Branch | Notes |
|------|--------|-------|
| Sidebar redesign | `feature/mediatrack-phase2` | Main frontend sidebar + team features |
| Admin dashboard | `feature/admin-web-design` | Independent `admin/` app |
| Database migrations | Both | admin-web uses `030_`, mediatrack uses `041_+` |

### Shared Data Layer

Both branches read/write the same tables:
- `teams`, `team_members`, `team_roles` — permission system
- `projects`, `project_members` — project access
- Payment/billing tables

### Merge Order

1. Merge `feature/admin-web-design` first (fewer conflicts with main frontend)
2. Merge `feature/mediatrack-phase2` second (major frontend changes)
3. Conflict files: only `App.tsx` and `backend/app/api/__init__.py`

---

## 9. Implementation Phases

### Phase 1: Foundation
- TeamSwitcher component
- Permission API endpoint
- Sidebar state machine (3 modes)
- Remove Team Library tab from Library

### Phase 2: Team Mode
- Projects list in sidebar
- Resources view (replaces Team Library)
- Members page (admin)
- Permission-based visibility

### Phase 3: Project Mode
- Immersive project sidebar
- File folder tree navigation
- Review section (annotations, approvals)
- Project activity timeline

### Phase 4: Billing
- Seat management UI
- WeChat/Alipay payment integration
- Tier gating in sidebar
