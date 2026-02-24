# Phase 1: Foundation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure MediaHub navigation from three-mode sidebar to workspace-based architecture with TopBar, WorkspaceSwitcher, and basic ResourcesView.

**Architecture:** Replace current TeamSwitcher + Sidebar with WorkspaceSwitcher (sidebar top) + TopBar (right icons) + workspace-aware navigation. Settings moves from sidebar to avatar menu. ResourcesView gets folder tree. All changes are frontend-only in Phase 1; backend API for resources/folders is minimal (Supabase direct queries via dataService).

**Tech Stack:** React 19, TypeScript, TailwindCSS, Lucide icons, Supabase JS, i18next

---

## Task 1: Update TypeScript Types

**Files:**
- Modify: `frontend/types.ts`

**Step 1: Add new types for resource library tables**

Add after the existing `CollectionVideo` interface (~line 235):

```typescript
// Folder (virtual folder tree)
export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  scope_type: 'personal' | 'team';
  scope_id: string;
  created_by: string;
  sort_order: number;
  is_system: boolean;
  icon: string | null;
  color: string | null;
  visibility: 'inherited' | 'restricted';
  is_trashed: boolean;
  trashed_at: string | null;
  created_at: string;
  updated_at: string;
  // Computed
  children?: Folder[];
  resource_count?: number;
}

// Resource (core resource record)
export interface Resource {
  id: string;
  creator_id: string;
  source_type: 'web' | 'upload';
  video_id: string | null;
  filename: string;
  file_type: string | null;
  mime_type: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  resolution: string | null;
  thumbnail_path: string | null;
  cover_image_path: string | null;
  current_version: number;
  is_trashed: boolean;
  trashed_at: string | null;
  created_at: string;
  updated_at: string;
  // Joined
  tags?: Tag[];
  folder_name?: string;
}

// Resource item (resource ↔ workspace mapping)
export interface ResourceItem {
  id: string;
  resource_id: string;
  scope_type: 'personal' | 'team';
  scope_id: string;
  folder_id: string | null;
  added_by: string | null;
  created_at: string;
  // Joined
  resource?: Resource;
}

// Resource version
export interface ResourceVersion {
  id: string;
  resource_id: string;
  version_number: number;
  filename: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  mime_type: string | null;
  duration_seconds: number | null;
  resolution: string | null;
  thumbnail_path: string | null;
  uploaded_by: string | null;
  notes: string | null;
  created_at: string;
}
```

**Step 2: Update ViewState to include new views**

Change the existing ViewState type:

```typescript
export type ViewState = 'parser' | 'library' | 'dashboard' | 'settings' | 'cleanup' | 'points' | 'mediatrack' | 'resources' | 'members' | 'billing' | 'todolist' | 'management';
```

Add `'todolist'` and `'management'` to the union.

**Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no type errors.

**Step 4: Commit**

```bash
git add frontend/types.ts
git commit -m "feat: add resource/folder types and new ViewState entries"
```

---

## Task 2: Create Resource & Folder Service Layer

**Files:**
- Create: `frontend/services/resourceService.ts`

**Step 1: Create resourceService.ts with Supabase queries**

```typescript
import { supabase } from '../supabaseClient';
import { Folder, Resource, ResourceItem } from '../types';

// ─── Folders ────────────────────────────────────────────

export async function fetchFolders(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<Folder[]> {
  const { data, error } = await supabase
    .from('folders')
    .select('*')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('is_trashed', false)
    .order('sort_order', { ascending: true });

  if (error) throw error;
  return data || [];
}

export async function createFolder(folder: {
  name: string;
  parent_id?: string | null;
  scope_type: 'personal' | 'team';
  scope_id: string;
}): Promise<Folder> {
  const user = (await supabase.auth.getUser()).data.user;
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('folders')
    .insert({ ...folder, created_by: user.id })
    .select()
    .single();

  if (error) throw error;
  return data;
}

export async function renameFolder(id: string, name: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ name })
    .eq('id', id);

  if (error) throw error;
}

export async function trashFolder(id: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ is_trashed: true, trashed_at: new Date().toISOString() })
    .eq('id', id);

  if (error) throw error;
}

// Build folder tree from flat list
export function buildFolderTree(folders: Folder[]): Folder[] {
  const map = new Map<string, Folder>();
  const roots: Folder[] = [];

  folders.forEach(f => map.set(f.id, { ...f, children: [] }));

  map.forEach(folder => {
    if (folder.parent_id && map.has(folder.parent_id)) {
      map.get(folder.parent_id)!.children!.push(folder);
    } else {
      roots.push(folder);
    }
  });

  return roots;
}

// ─── Resources ──────────────────────────────────────────

export async function fetchResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

  if (folderId) {
    query = query.eq('folder_id', folderId);
  } else {
    query = query.is('folder_id', null);
  }

  const { data, error } = await query.order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

export async function fetchResourceCount(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<number> {
  const { count, error } = await supabase
    .from('resource_items')
    .select('*', { count: 'exact', head: true })
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

  if (error) throw error;
  return count || 0;
}
```

**Step 2: Verify build**

Run: `cd frontend && npm run build`

**Step 3: Commit**

```bash
git add frontend/services/resourceService.ts
git commit -m "feat: add resource/folder service layer for Supabase queries"
```

---

## Task 3: Refactor WorkspaceSwitcher

**Files:**
- Modify: `frontend/components/TeamSwitcher.tsx` → rename to `WorkspaceSwitcher.tsx`
- Modify: `frontend/components/Sidebar.tsx` — update import

**Step 1: Create WorkspaceSwitcher.tsx**

Copy `TeamSwitcher.tsx` to `WorkspaceSwitcher.tsx`, then update:

- Add plan tier badge next to team name (e.g., "Free", "Pro")
- Change "Personal" label to "{username}'s Workspace"
- Keep same props interface but rename to `WorkspaceSwitcherProps`
- Accept optional `userName` prop for personal workspace label

Key changes to the component:
- Props: add `userName?: string`
- Personal label: `{userName}'s Workspace` instead of "Personal"
- Team items: add plan tier badge `<span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-700 text-zinc-400">Free</span>`

**Step 2: Update Sidebar.tsx import**

Change `import { TeamSwitcher }` to `import { WorkspaceSwitcher }` and update all usages.

**Step 3: Update App.tsx**

Pass `userName` prop from user profile to Sidebar, which passes it to WorkspaceSwitcher.

**Step 4: Verify build**

Run: `cd frontend && npm run build`

**Step 5: Commit**

```bash
git add frontend/components/WorkspaceSwitcher.tsx frontend/components/Sidebar.tsx frontend/App.tsx
git commit -m "feat: rename TeamSwitcher to WorkspaceSwitcher with plan tier badge"
```

---

## Task 4: Create TopBar Component

**Files:**
- Create: `frontend/components/TopBar.tsx`

**Step 1: Create TopBar with 4 icon buttons**

```
[page content area]                    [🔍] [📋] [🔔] [👤]
```

The TopBar sits at the top-right of the main content area. Contains:
- **SearchButton**: Opens Cmd+K search (placeholder for now)
- **TaskCenterButton**: Opens floating panel with Transfers/Tasks tabs
- **NotificationButton**: Bell icon with unread badge
- **AvatarMenu**: User avatar dropdown (Profile, Settings, Sign out)

Props:
```typescript
interface TopBarProps {
  user: { name: string; email: string; avatarUrl?: string } | null;
  onNavigate: (view: string) => void;
  onSignOut: () => void;
}
```

**Step 2: Implement AvatarMenu dropdown**

Dropdown items:
- Profile → `onNavigate('settings')` with `settingsTab = 'profile'`
- Settings → `onNavigate('settings')` with `settingsTab = 'general'`
- Notifications → toggle notification panel
- Divider
- Help & Docs → external link
- Feedback → external link
- Divider
- Sign out → `onSignOut()`

**Step 3: Implement TaskCenter floating panel (stub)**

Two tabs: Transfers / Tasks. Both show empty state for now.
Opens as a fixed-position panel below the icon.

**Step 4: Verify build**

Run: `cd frontend && npm run build`

**Step 5: Commit**

```bash
git add frontend/components/TopBar.tsx
git commit -m "feat: add TopBar component with avatar menu, task center, notifications"
```

---

## Task 5: Refactor Sidebar Navigation

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

This is the largest task. The sidebar navigation items change based on workspace type.

**Step 1: Update Personal workspace navigation**

Remove: Dashboard, Settings submenu, Library submenu (Smart Collections, Storage Cleanup)
Add: Resources, Projects, Todolist
Keep: Parser (personal only)

Personal workspace nav:
```
Parser
Resources
Projects
Todolist
```

**Step 2: Update Team workspace navigation**

Remove: Parser, My Library, Settings submenu
Add: Resources, Projects, Todolist, Management
Keep: existing team items restructured

Team workspace nav:
```
Resources
Projects
Todolist
Management
```

**Step 3: Keep Project mode sidebar unchanged**

The immersive project mode sidebar (back button, files, review, members) stays as-is for now.

**Step 4: Remove Settings from sidebar**

Settings is now in the TopBar avatar menu, not in the sidebar. Remove `renderSettingsMenu()` from personal and team modes.

**Step 5: Update Library handling**

"My Library" becomes part of Resources view. Remove the library submenu toggle, SmartCollectionsSidebar, and Storage Cleanup from sidebar. These will be accessible within ResourcesView in Phase 2.

**Step 6: Verify build**

Run: `cd frontend && npm run build`

**Step 7: Commit**

```bash
git add frontend/components/Sidebar.tsx
git commit -m "feat: restructure sidebar nav — Parser/Resources/Projects/Todolist/Management"
```

---

## Task 6: Create Basic ResourcesView (New)

**Files:**
- Modify: `frontend/components/ResourcesView.tsx` (replace existing)

**Step 1: Rewrite ResourcesView with folder tree**

The new ResourcesView has:
- Left panel: folder tree (collapsible)
- Right panel: resource grid/list
- Header: breadcrumb + view toggle (grid/list) + "New" button (upload/new folder)

Props:
```typescript
interface ResourcesViewProps {
  scopeType: 'personal' | 'team';
  scopeId: string;
}
```

**Step 2: Implement FolderTree sub-component**

Inline within ResourcesView or extracted:
- Recursive folder rendering with expand/collapse
- "All Resources" root item
- Click folder → filter resources
- Right-click → rename/delete (future)

**Step 3: Implement resource grid**

- Grid of cards: thumbnail + filename + file type icon + size + date
- Empty state: "No resources yet. Upload files or parse videos to get started."
- Loading state with skeleton

**Step 4: Wire up to resourceService**

Use `fetchFolders()` and `fetchResources()` from Task 2.

**Step 5: Verify build**

Run: `cd frontend && npm run build`

**Step 6: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat: rewrite ResourcesView with folder tree and resource grid"
```

---

## Task 7: Integrate into App.tsx

**Files:**
- Modify: `frontend/App.tsx`

**Step 1: Add TopBar to layout**

Insert TopBar component above the main content area, inside the `ml-64` wrapper (to the right of sidebar).

```tsx
<div className="ml-64 flex flex-col min-h-screen">
  <TopBar
    user={userProfile}
    onNavigate={(v) => handleViewChange(v)}
    onSignOut={handleSignOut}
  />
  <main className="flex-1 p-8">
    {/* existing view routing */}
  </main>
</div>
```

**Step 2: Update view routing**

Add cases for new views in the main render:
- `'todolist'` → placeholder "Coming soon" view
- `'management'` → placeholder with link to existing Members/Billing
- `'resources'` → new ResourcesView with proper scopeType/scopeId

Pass correct scopeType/scopeId to ResourcesView:
```tsx
{view === 'resources' && (
  <ResourcesView
    scopeType={selectedTeamId ? 'team' : 'personal'}
    scopeId={selectedTeamId || userId}
  />
)}
```

**Step 3: Pass userName to Sidebar → WorkspaceSwitcher**

Extract user's display name from `userProfile` and thread it through.

**Step 4: Update onTeamChange default view**

When switching workspace:
- Personal → default to `'parser'`
- Team → default to `'resources'` (not `'mediatrack'`)

**Step 5: Verify build**

Run: `cd frontend && npm run build`

**Step 6: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: integrate TopBar, new ResourcesView, and workspace navigation into App"
```

---

## Task 8: Cleanup & Final Build

**Files:**
- Possibly: remove unused imports, dead code

**Step 1: Remove dead code**

- Remove old `TeamSwitcher.tsx` if fully replaced
- Remove unused imports in Sidebar.tsx (Dashboard, Library related icons if no longer used)
- Keep `LibraryFeed.tsx`, `LibraryTable.tsx` etc. — they'll be reused in ResourcesView later

**Step 2: Final build check**

Run: `cd frontend && npm run build`
Expected: Clean build with no errors or warnings.

**Step 3: Manual smoke test**

Run: `cd frontend && npm run dev`
Verify:
- [ ] WorkspaceSwitcher shows personal + team workspaces
- [ ] Personal workspace: Parser / Resources / Projects / Todolist in sidebar
- [ ] Team workspace: Resources / Projects / Todolist / Management in sidebar
- [ ] TopBar visible with Search / TaskCenter / Notifications / Avatar icons
- [ ] Avatar menu opens with Profile / Settings / Sign out
- [ ] ResourcesView loads (empty state is OK)
- [ ] Project mode sidebar still works (click into a project)

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: cleanup dead code and unused imports from Phase 1 refactor"
```

---

## Summary

| Task | Description | Est. Size |
|------|-------------|-----------|
| 1 | TypeScript types | Small |
| 2 | Resource/folder service layer | Small |
| 3 | WorkspaceSwitcher (rename + enhance) | Small |
| 4 | TopBar component | Medium |
| 5 | Sidebar navigation refactor | Large |
| 6 | ResourcesView rewrite | Medium |
| 7 | App.tsx integration | Medium |
| 8 | Cleanup & final build | Small |

Total: 8 tasks, ~8 commits
