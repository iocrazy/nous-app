# Route Architecture Redesign: Downloads Scope + Permissions + Unified Experience

## Goal

Isolate Downloads to personal workspace, add resource-level permission checks on `/media/{id}`, unify detail page routing, and adapt mobile tab bar to workspace context.

## Decisions

### 1. Downloads Scope

Downloads is a **sub-section of Resources**, visible only in personal workspace.

- **Personal workspace**: ResourcesView sidebar shows "我的下载" + "我的资源"
- **Team workspace**: ResourcesView sidebar shows Libraries only (no Downloads)
- **Data query**: Unchanged — `resources WHERE creator_id = user_id AND source_type = 'web'`
- **Desktop sidebar**: No standalone "Downloads" menu item; accessed via Resources → Downloads sub-view

### 2. Mobile Tab Bar (Context-Aware)

Tab bar changes based on current workspace:

**Personal workspace (4 tabs):**
```
[ Parser ] [ Downloads ] [ Resources ] [ Me ]
```

**Team workspace (3 tabs):**
```
[ Parser ] [ Resources ] [ Me ]
```

- Downloads tab only appears in personal workspace
- Me tab expands to include: personal profile, team switching, settings

### 3. Me Tab Expansion

Current: Opens UserProfileModal only.

New: Opens a full page/panel with:
- **Profile section**: Avatar, name, email
- **Workspace switcher**: List of personal + team workspaces, tap to switch
- **Settings**: Link to settings page
- **Logout**: Sign out button

### 4. Resource-Level Permission Check

Current: `/media/{id}` only checks if user is authenticated (any logged-in user can access any media).

New: `/media/{id}` checks access authorization:

```
1. resources.creator_id = user_id → ALLOW (own resource)
2. resource belongs to a team where user is a member → ALLOW (team resource)
3. valid share_token in query params → ALLOW (shared link)
4. none of the above → 403 Forbidden
```

Implementation:
- Capture `user_id` from `_authenticate_media_request()` (currently discarded)
- Extend `_resolve_file_path()` to return `(file_path, creator_id, team_ids)` tuple instead of just `file_path`
- Create `app/api/media_permissions.py` with `check_media_access(user_id, creator_id, team_ids)` as a FastAPI dependency
- Cache `(file_path, creator_id, team_ids)` in the 5-min TTL cache (key: `media_id`)
- If resource ownership changes, stale cache may grant wrong access for up to 5 minutes — acceptable trade-off
- `share_token` validation: **out of scope** for this phase — left as TODO placeholder. Current share links use `/share/:shareCode` page which fetches data via API (not `/media/` route), so no breakage
- Legacy `/media/{file_path:path}` route: **deprecate** — return 301 redirect to `/media/{id}` by doing a reverse lookup (`resources WHERE file_path = ?`). Add `file_path` index if not exists. If reverse lookup fails, serve file without permission check (backward compat grace period)

**Phase split**: Permission checks are Phase B (backend). Frontend changes are Phase A.

### 5. Detail Page Routing

**Unified route**: `/resources/file/{resourceId}` (no other detail routes)

**Component hierarchy:**
```
router.tsx
  └─ /resources/file/:resourceId → FileDetailDispatcher
      ├─ source_type = 'web' → DownloadDetailPage (platform stats, refetch, etc.)
      └─ source_type != 'web' → ResourceDetailPage (versions, tags, notes, etc.)
```

**File renames:**
- `pages/ResourceDetailPage.tsx` → `pages/FileDetailDispatcher.tsx` (the router entry, 69 lines)
- Current `components/ResourceDetail.tsx` → rename to `components/ResourceDetailPage.tsx`
- `pages/DownloadDetailPage.tsx` — already named correctly

**Deleted routes:**
- `/player/:displayId` — remove from router.tsx entirely
- `/library` and `/library/:itemId` — keep as redirects to `/resources/downloads` (backward compat)

### 6. Route Map (After Redesign)

**Team-scoped routes (`/team/:teamId/`):**

| Route | Component | Scope |
|-------|-----------|-------|
| `parser` | ParserPage | all |
| `downloads` | redirect → `resources/downloads` | alias for mobile tab |
| `resources` | ResourcesPage | all |
| `resources/downloads` | ResourcesPage (downloads sub-view) | personal only |
| `resources/shared` | ResourcesPage (shared sub-view) | all |
| `resources/recycle` | ResourcesPage (recycle sub-view) | all |
| `resources/file/:resourceId` | FileDetailDispatcher | all |
| `resources/folder/:folderId` | ResourcesPage | all |
| `resources/library/:libraryId` | ResourcesPage | team only |
| `resources/library/:libraryId/folder/:folderId` | ResourcesPage | team only |
| `resources/smart/:smartFolderId` | ResourcesPage | all |
| `projects` | ProjectsPage | all |
| `projects/:projectId` | ProjectsPage | all |
| `settings` | SettingsPage | all |
| `cleanup` | CleanupPage | personal |
| `points` | PointsPage | personal |
| `members` | MembersPage | team |
| `billing` | BillingPage | team |
| `todolist` | TodolistPage | all |
| `shared` | SharedPage | all |

**Removed routes:**
- `player/:displayId` — deleted
- `library` / `library/:itemId` — redirect to `downloads`
- `dashboard` / `dashboard/:subview` — keep (active feature, DashboardPage is imported and used)

### 7. ViewState Cleanup

Current `ViewState` type has stale values. After redesign:

```typescript
export type ViewState =
  | 'parser'
  | 'resources'    // includes downloads/shared/recycle sub-views
  | 'mediatrack'   // projects
  | 'settings'
  | 'cleanup'
  | 'points'
  | 'members'
  | 'billing'
  | 'todolist'
  | 'shared'
  | 'dashboard';

// Remove: 'library' (merged into resources), 'management' (unused)
```

### 8. Centralized Route Mapping

Current problem: `pathnameToView()` duplicated in 3 places (AppLayout, Sidebar, ResourcesView).

Fix: Create `frontend/utils/routeConfig.ts`:

```typescript
export const ROUTE_MAP: Record<ViewState, string> = {
  parser: '/parser',
  resources: '/resources',
  mediatrack: '/projects',
  dashboard: '/dashboard',
  // ...
};

export function pathnameToView(pathname: string): ViewState {
  // Single source of truth
}
```

All 3 consumers import from this module.

### 9. Team Downloads URL Handling

When a user navigates to `/team/{teamId}/resources/downloads` in a team workspace:
- ResourcesView detects `scopeType === 'team'` + `section === 'downloads'`
- Redirects to `/team/{teamId}/resources` (team's default resource view)
- No error message needed — silent redirect

When mobile Downloads tab is tapped in team workspace:
- Tab is not rendered (3-tab layout), so this case doesn't arise

## Files to Change

### New Files
- `frontend/utils/routeConfig.ts` — centralized route mapping
- `frontend/pages/FileDetailDispatcher.tsx` — rename from current ResourceDetailPage

### Modified Files (Frontend)
- `frontend/router.tsx` — remove `/player/:displayId`, update component imports
- `frontend/components/AppLayout.tsx` — dynamic tab bar, Me tab expansion, use `routeConfig`
- `frontend/components/Sidebar.tsx` — use `routeConfig`, remove `library` view
- `frontend/components/ResourcesView.tsx` — redirect team + downloads to resources
- `frontend/pages/ResourceDetailPage.tsx` — rename from current ResourceDetail component
- `frontend/types.ts` — clean up ViewState type

### Modified Files (Backend)
- `backend/app/main.py` — add `_check_access()` to `/media/{id}` and `/media/{file_path:path}` routes

### Deleted Files
- `frontend/pages/LibraryPage.tsx` — dead code (no longer imported)

### 10. Workspace Switch Transition

When user switches from personal to team workspace (via Me tab workspace switcher):
- If currently on `/downloads` or `/resources/downloads` → navigate to `/team/{newTeamId}/resources`
- If on any other view → navigate to `/team/{newTeamId}/{currentView}`
- Tab bar immediately renders 3-tab layout (no flash of 4 tabs)

### 11. ResourcesView Shell Refactor

**Current problem**: `ResourcesView.tsx` is 3000+ lines handling layout, routing, content rendering, state management, drag-drop, context menus, and info panel all in one file. Mobile/desktop differences are scattered across conditional rendering.

**Pattern**: Responsive Shell — industry standard (Notion, Linear, Figma):

```
ResourcesContext (Layer 2: shared domain state)
  │
  └─ ResourcesShell (responsive layout adapter)
      ├─ Desktop: ResourcesSidebar + ContentArea + ResourcesInfoPanel
      └─ Mobile: ContentArea full-screen + floating actions
          │
          ContentArea (selected by URL section):
          ├─ /resources/downloads → DownloadsView
          ├─ /resources (default) → ResourceGrid
          ├─ /resources/shared → SharedView
          └─ /resources/recycle → RecycleView
```

**Three-layer state management** (big-company standard):
- **Layer 1 — URL**: navigation state (`sidebarView`, `folderId`, `libraryId`) — already URL-driven, no change
- **Layer 2 — ResourcesContext**: shared business data (`resources`, `folders`, `libraries`, `selectedResource`, `selectedIds`) + actions (`loadResources`, `handleDelete`, `handleMove`)
- **Layer 3 — Component useState**: pure UI state (dropdown open, dragging, panel width)

**File split target:**

| New File | Lines | Responsibility |
|----------|-------|---------------|
| `contexts/ResourcesContext.tsx` | ~200 | Shared state provider: data, selection, actions |
| `components/ResourcesShell.tsx` | ~150 | Responsive layout: sidebar/content/panel arrangement |
| `components/ResourcesSidebar.tsx` | ~300 | Desktop sidebar: shared/recycle/downloads/libraries/smart-folders |
| `components/ResourceGrid.tsx` | ~600 | File/folder grid with context menus, drag-drop, multi-select |
| `components/SharedView.tsx` | ~200 | Shared resources view |
| `components/RecycleView.tsx` | ~200 | Recycle bin view |
| `components/ResourcesInfoPanel.tsx` | ~300 | Right-side info panel (tags, notes, properties) |
| `components/DownloadsView.tsx` | ~1100 | Already exists, no change |

**What does NOT change:**
- All URLs/routes stay the same
- Desktop UI looks identical
- Mobile UI looks identical
- All features (upload, drag-drop, multi-select, tags, batch operations) preserved
- API calls unchanged
- Other pages unaffected

**Refactor strategy**: Extract one piece at a time, verify build + visual after each. Start with Context (state), then Shell (layout), then Sidebar, then content views.

### 12. Implementation Phases

**Phase A (Frontend)**: Decisions 1-3, 5-11 — route cleanup, tab bar, Me tab, Shell refactor
**Phase B (Backend)**: Decision 4 — permission checks, needs `media_permissions.py`, DB index for file_path

Phase A can ship independently. Phase B requires more design detail for edge cases.

## Success Criteria

- Downloads only visible in personal workspace (desktop sidebar + mobile tab)
- Team workspace has no Downloads entry anywhere
- Mobile tab bar adapts: 4 tabs (personal) vs 3 tabs (team)
- Me tab shows profile + workspace switcher + settings
- `/media/{id}` rejects access to resources user doesn't own/have team access to
- All detail pages use `/resources/file/{resourceId}` route
- No `/player/` routes exist
- `pathnameToView()` defined in one place, imported everywhere
- `ResourcesView.tsx` split into 7+ focused modules (no file > 800 lines)
- ResourcesContext provides shared state to all resource sub-components
