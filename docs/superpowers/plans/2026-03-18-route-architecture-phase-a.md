# Route Architecture Redesign — Phase A Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor ResourcesView (3000 lines) into a responsive Shell architecture with ResourcesContext, isolate Downloads to personal workspace, adapt mobile tab bar, expand Me tab, unify detail page routing, and centralize route mapping.

**Architecture:** Three-layer state (URL + ResourcesContext + useState). ResourcesShell handles responsive layout. Content components (ResourceGrid, DownloadsView, SharedView, RecycleView) are pure content. Mobile tab bar is context-aware (4 tabs personal, 3 tabs team).

**Tech Stack:** React 19, TypeScript, React Router v6, TailwindCSS

**Spec:** `docs/superpowers/specs/2026-03-18-route-architecture-redesign.md`

---

## Stage 1: Route Infrastructure (Tasks 1-4)

These tasks can ship independently without breaking anything.

### Task 1: Create `routeConfig.ts` — centralized route mapping

**Files:**
- Create: `frontend/utils/routeConfig.ts`
- Modify: `frontend/types.ts:124` — clean ViewState
- Modify: `frontend/components/AppLayout.tsx:30-52, 156-168` — replace local mapping
- Modify: `frontend/components/Sidebar.tsx:184-215` — replace local mapping

- [ ] **Step 1: Create `routeConfig.ts` and update ViewState**

Create `frontend/utils/routeConfig.ts` with `VIEW_PATH_MAP` and `pathnameToView()`.

Update `frontend/types.ts:124` — remove `'library'` and `'management'` from ViewState.

Replace local `pathnameToView` in AppLayout (lines 30-52) and `viewFromPathname` in Sidebar (lines 199-215) with imports from routeConfig. Replace local `VIEW_PATH_MAP` in Sidebar (lines 184-197) and `viewToPath` in AppLayout (lines 156-168) with the shared one.

**WARNING**: `'library'` also appears as a module key in `hooks/useTeams.ts:31` and `hooks/useTeams.ts:44` — DO NOT change those (feature flags, not ViewState).

- [ ] **Step 2: Verify build**

Run: `cd frontend && npx tsc --noEmit && npm run build`

- [ ] **Step 3: Commit**

```bash
git add frontend/utils/routeConfig.ts frontend/types.ts frontend/components/AppLayout.tsx frontend/components/Sidebar.tsx
git commit -m "feat: centralize route mapping in routeConfig.ts, clean ViewState"
```

---

### Task 2: Dynamic mobile tab bar + Downloads scope

**Files:**
- Modify: `frontend/components/AppLayout.tsx` — mobile tab bar (~lines 245-354)
- Modify: `frontend/components/ResourcesView.tsx:187-189` — team downloads redirect

- [ ] **Step 1: Mobile tab bar — hide Downloads in team workspace**

In AppLayout, add `isPersonalWorkspace` check after line 79:
```typescript
const isPersonalWorkspace = !selectedTeamId || selectedTeamId === personalTeamId;
```

Wrap the Downloads tab button (~lines 267-273) with `{isPersonalWorkspace && (...)}`.

Update `handleMobileLibraryClick` to always go to personal workspace downloads:
```typescript
const handleMobileLibraryClick = () => {
  const pid = personalTeamId || selectedTeamId;
  navigate(`/team/${pid}/resources/downloads`);
};
```

- [ ] **Step 2: Team downloads URL redirect**

In ResourcesView.tsx, after the sidebarView derivation (line 189), add a useEffect:
```typescript
useEffect(() => {
  if (sidebarView === 'downloads' && scopeType === 'team') {
    navigate(resPath('/resources'), { replace: true });
  }
}, [sidebarView, scopeType]);
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`

- [ ] **Step 4: Commit**

```bash
git add frontend/components/AppLayout.tsx frontend/components/ResourcesView.tsx
git commit -m "feat: dynamic mobile tab bar, redirect team downloads to resources"
```

---

### Task 3: Me Tab expansion (MobileProfilePage)

**Files:**
- Create: `frontend/components/MobileProfilePage.tsx`
- Modify: `frontend/components/AppLayout.tsx` — Me tab click handler

- [ ] **Step 1: Create MobileProfilePage**

Create `frontend/components/MobileProfilePage.tsx` with:
- Profile section (avatar, name, email)
- Workspace switcher (list teams, tap to switch, check mark on active)
- Settings link
- Logout button
- Workspace switch logic: if on downloads and switching to team → navigate to `/resources`

- [ ] **Step 2: Wire into AppLayout**

Add `isMobileProfileOpen` state. Replace Me tab's `setIsProfileModalOpen(true)` with `setIsMobileProfileOpen(true)`. Render `MobileProfilePage` when open.

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`

- [ ] **Step 4: Commit**

```bash
git add frontend/components/MobileProfilePage.tsx frontend/components/AppLayout.tsx
git commit -m "feat: Me tab expands to profile page with workspace switcher"
```

---

### Task 4: Detail page routing cleanup

**Files:**
- Rename: `frontend/pages/ResourceDetailPage.tsx` → `frontend/pages/FileDetailDispatcher.tsx`
- Rename: `frontend/components/ResourceDetail.tsx` → `frontend/components/ResourceDetailPage.tsx`
- Modify: `frontend/router.tsx` — remove `/player/:displayId`, update imports, add `/downloads` alias

- [ ] **Step 1: Rename files with git mv**

```bash
git mv frontend/pages/ResourceDetailPage.tsx frontend/pages/FileDetailDispatcher.tsx
git mv frontend/components/ResourceDetail.tsx frontend/components/ResourceDetailPage.tsx
```

- [ ] **Step 2: Update all imports**

In `FileDetailDispatcher.tsx`: import `ResourceDetailPage` from `../components/ResourceDetailPage`, rename component to `FileDetailDispatcher`.

In `router.tsx`: import `FileDetailDispatcher` instead of `ResourceDetailPage`. Remove `DownloadDetailPage` import and `/player/:displayId` route. Add `{ path: 'downloads', element: <Navigate to="resources/downloads" replace /> }`.

In `ResourceDetailPage.tsx` (the component): rename export from `ResourceDetail` to `ResourceDetailPage`. Note: actual export is `export const ResourceDetail: React.FC<ResourceDetailProps>` — update to `ResourceDetailPage`.

Update all other files that import `ResourceDetail` from `../components/ResourceDetail`.

- [ ] **Step 3: Delete LibraryPage**

```bash
rm frontend/pages/LibraryPage.tsx
```

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`

- [ ] **Step 5: Commit**

```bash
git add -A frontend/pages/ frontend/components/ResourceDetailPage.tsx frontend/router.tsx
git commit -m "refactor: FileDetailDispatcher + ResourceDetailPage rename, remove /player route, delete LibraryPage"
```

---

## Stage 2: ResourcesView Shell Refactor (Tasks 5-9)

Extract ResourcesView (3000 lines) into focused modules. Each task extracts one piece, builds, and verifies no visual changes.

### Task 5: Extract ResourcesContext

**Files:**
- Create: `frontend/contexts/ResourcesContext.tsx`
- Modify: `frontend/components/ResourcesView.tsx` — move state to context

**Context:** ResourcesView holds ~30 state variables and ~20 handler functions. Extract the shared ones into a context provider.

- [ ] **Step 1: Create ResourcesContext**

Create `frontend/contexts/ResourcesContext.tsx`. Move these from ResourcesView into the context:

**State to extract:**
- `resources`, `folders`, `libraries`, `smartFolders` — data
- `selectedResource`, `selectedIds`, `selectedFolder` — selection
- `trashedResources`, `trashedFolders` — recycle bin data
- `isLoading`, `error` — loading state
- `scopeType`, `scopeId` — passed as props, forwarded via context

**Actions to extract:**
- `loadResources()`, `loadFolders()`, `loadTrashedResources()`
- `handleDeleteResource()`, `handleMoveResource()`, `handleRestoreResource()`
- `setSelectedIds()`, `setSelectedResource()`

The context provider wraps ResourcesView's children and receives `scopeType`/`scopeId` as props.

- [ ] **Step 2: Update ResourcesView to use context**

Replace direct state usage with `useResourcesContext()` hook calls. ResourcesView becomes the provider wrapper + layout.

- [ ] **Step 3: Verify build + visual**

Run: `cd frontend && npm run build`
Visually verify: desktop Resources page looks identical, all interactions work.

- [ ] **Step 4: Commit**

```bash
git add frontend/contexts/ResourcesContext.tsx frontend/components/ResourcesView.tsx
git commit -m "refactor: extract ResourcesContext for shared state management"
```

---

### Task 6: Extract ResourcesSidebar

**Files:**
- Create: `frontend/components/ResourcesSidebar.tsx`
- Modify: `frontend/components/ResourcesView.tsx` — replace inline sidebar JSX

- [ ] **Step 1: Extract sidebar JSX**

Move the desktop sidebar section (~lines 2195-2412 of current ResourcesView) into `ResourcesSidebar.tsx`. It reads from `useResourcesContext()` for data and uses URL navigation for view switching.

The sidebar contains: Shared / Recycle / Downloads (personal only) / My Resources / Libraries (team) / Smart Folders.

- [ ] **Step 2: Replace in ResourcesView**

Replace the inline sidebar JSX with `<ResourcesSidebar />`.

- [ ] **Step 3: Verify build + visual**

Desktop sidebar must look and behave identically.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourcesSidebar.tsx frontend/components/ResourcesView.tsx
git commit -m "refactor: extract ResourcesSidebar from ResourcesView"
```

---

### Task 7: Extract ResourcesInfoPanel

**Files:**
- Create: `frontend/components/ResourcesInfoPanel.tsx`
- Modify: `frontend/components/ResourcesView.tsx` — replace inline info panel JSX

- [ ] **Step 1: Extract info panel**

Move the right-side info panel (~lines 3189-3400 of current ResourcesView) into `ResourcesInfoPanel.tsx`. It reads `selectedResource`/`selectedFolder` from context.

- [ ] **Step 2: Replace in ResourcesView**

Replace inline info panel with `<ResourcesInfoPanel />`.

- [ ] **Step 3: Verify build + visual**

Info panel must show correctly when selecting a resource.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourcesInfoPanel.tsx frontend/components/ResourcesView.tsx
git commit -m "refactor: extract ResourcesInfoPanel from ResourcesView"
```

---

### Task 8: Extract ResourceGrid (main content area)

**Files:**
- Create: `frontend/components/ResourceGrid.tsx`
- Modify: `frontend/components/ResourcesView.tsx` — replace inline content

- [ ] **Step 1: Extract grid content**

Move the main resource grid/list rendering (~lines 2484-3100 of current ResourcesView) into `ResourceGrid.tsx`. This includes:
- Grid/list/compact view modes
- File and folder cards
- Context menus
- Multi-select checkbox overlay
- Drag-and-drop handlers
- Empty state
- Batch action toolbar

It reads data from `useResourcesContext()`.

- [ ] **Step 2: Replace in ResourcesView**

Replace inline content with `<ResourceGrid />` for the `isResourcesView` condition.

- [ ] **Step 3: Verify build + visual**

All resource grid interactions must work: click, double-click, right-click, drag, multi-select, batch delete.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourceGrid.tsx frontend/components/ResourcesView.tsx
git commit -m "refactor: extract ResourceGrid from ResourcesView"
```

---

### Task 9: Create ResourcesShell (responsive layout adapter)

**Files:**
- Create: `frontend/components/ResourcesShell.tsx`
- Modify: `frontend/components/ResourcesView.tsx` — final cleanup into shell
- Modify: `frontend/pages/ResourcesPage.tsx` — wrap with context provider

**Context:** After Tasks 5-8, ResourcesView still holds the layout logic (sidebar + content + info panel arrangement). Extract this into ResourcesShell, making ResourcesView just a thin wrapper.

- [ ] **Step 1: Create ResourcesShell**

`ResourcesShell.tsx` handles:
- Desktop: `flex` layout with `ResourcesSidebar` + content slot + `ResourcesInfoPanel`
- Mobile: content slot only, full-screen
- Uses `useMediaQuery` or CSS `hidden md:flex` for responsive behavior

```tsx
export function ResourcesShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex-1 flex h-full">
      {/* Desktop sidebar */}
      <div className="hidden md:flex md:w-56 shrink-0 border-r border-zinc-800/80">
        <ResourcesSidebar />
      </div>
      {/* Content */}
      <div className="flex-1 min-w-0">{children}</div>
      {/* Desktop info panel */}
      <ResourcesInfoPanel />
    </div>
  );
}
```

- [ ] **Step 2: Simplify ResourcesView**

ResourcesView becomes:
```tsx
export function ResourcesView({ scopeType, scopeId }: Props) {
  return (
    <ResourcesProvider scopeType={scopeType} scopeId={scopeId}>
      <ResourcesShell>
        {/* Content by sidebarView */}
        {isDownloadsView ? <DownloadsView /> : isSharedView ? <SharedView /> : isRecycleView ? <RecycleView /> : <ResourceGrid />}
      </ResourcesShell>
    </ResourcesProvider>
  );
}
```

Target: ResourcesView should be under 100 lines.

- [ ] **Step 3: Verify build + full visual test**

Test ALL views: Resources grid, Downloads, Shared, Recycle, folder navigation, info panel, context menus, drag-drop, mobile rendering.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourcesShell.tsx frontend/components/ResourcesView.tsx frontend/pages/ResourcesPage.tsx
git commit -m "refactor: ResourcesShell responsive layout, ResourcesView now thin wrapper"
```

---

## Final Verification

- [ ] **Step 1: Full production build**

```bash
cd frontend && npm run build
```

- [ ] **Step 2: Visual smoke test**

Test checklist:
- [ ] Desktop: Resources grid, sidebar navigation, info panel
- [ ] Desktop: Downloads sub-view (personal workspace)
- [ ] Desktop: Team workspace has no Downloads in sidebar
- [ ] Desktop: Shared view, Recycle view
- [ ] Desktop: Folder navigation, breadcrumbs
- [ ] Desktop: Context menus, drag-drop, multi-select
- [ ] Desktop: `/resources/file/{id}` → correct detail page
- [ ] Mobile: 4 tabs in personal workspace
- [ ] Mobile: 3 tabs in team workspace
- [ ] Mobile: Me tab shows profile + workspace switcher
- [ ] Mobile: Downloads full-screen (no sidebar chrome)
- [ ] `/player/{id}` → 404 (removed)
- [ ] `/library` → redirects to `/resources/downloads`
