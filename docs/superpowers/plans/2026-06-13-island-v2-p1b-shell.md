# Island Redesign v2 — P1b: Island App Shell (AppLayout / TopBar / Sidebar frame)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the desktop app frame into the spec's "island" layout — a full-width global top bar + a floating nav island + a floating workspace island over a background micro-glow — behind a `VITE_FEATURE_ISLAND_UI` flag, reusing the existing Sidebar / TopBar / Outlet with **zero feature change** (spec D12).

**Architecture:** The island frame is a *layout mode*, not a rewrite. A new `IslandShell.tsx` owns the flex frame (glow background → topbar row → content row with nav-island + work-island, 10–12px gaps) and the island card chrome (`bg-island` + `border-line` + `rounded-[18px]` + shadow). The existing `Sidebar` and `TopBar` gain a single `island?: boolean` prop that swaps **only their root wrapper** from `position:fixed` + hard widths to `relative w-full h-full` (filling the island the shell draws) — all their inner content, panels, popups, workspace switcher, and feature logic stay byte-identical. `AppLayout` chooses `IslandShell` vs the current frame by the flag. Mobile (`hidden sm:flex` Sidebar/TopBar + `MobileTabBar` + mobile overlays) is **untouched** in both modes. Detail routes auto-collapse the nav island to a 54px icon rail (spec D5) — island-mode only.

**Tech Stack:** React 19 + TS, react-router-dom v6 (`<Outlet>`), Tailwind v4 (semantic island tokens already in `index.css`: `--island`, `--island-2`, `--line`, `--line-strong`, `--app-bg`, `--content*`, `--color-island` etc.), vitest + @testing-library/react, Vite env flags.

**Hard constraints (spec):**
- **D12 — zero feature change.** Every interaction in the inventory checklist below must work identically. The flag-OFF path must render the *current* DOM (no churn).
- **D5 — main nav never disappears.** Detail routes shrink the nav island to a 54px icon rail (hover tooltips), they do NOT hide it.
- **D4 — no emoji.** Lucide icons only (brand mark is a gradient square, no emoji).
- **Density not reduced; navigation not removed.** No nav item, count, badge, or affordance dropped.
- tsc error-line baseline **140** must not increase. vitest must stay green (current **932** passed); add new tests on top.
- New code uses `ink-*` / semantic tokens only — **no `*-zinc-*`** (CI guard `scripts/check-no-zinc.sh`).
- Commit each task.

**Out of scope (later phases):**
- Per-page secondary rail moving *inside* the work island, resizable splitter, detail info-island, collapse-to-handle — these are **P2 (resources)** / **P3 (video)** / **P4 (audio)**. P1b draws the work island as a plain card wrapping today's `<Outlet>` content unchanged.
- Mobile redesign (MobileTabBar / MobileProfilePage / MobileTasksPage stay as-is).
- Cmd+K search (TopBar search button stays the existing Phase-2 stub).
- Audio cover-tint, comet back-button (P3/P4).

---

## D12 Feature-Parity Checklist (acceptance baseline)

Derived from the live inventory of `AppLayout.tsx` (567L), `TopBar.tsx` (576L), `Sidebar.tsx` + `WorkspaceSwitcher.tsx` + `sidebar/SidebarSection.tsx`. **Every item must behave identically with the flag ON.** Re-verify each after Task 6.

**AppLayout orchestration (must remain mounted & functional in island mode):**
- [ ] Provider stack: `LibraryProvider › ToastProvider › ConfirmProvider › TaskManagerProvider › UploadProvider`.
- [ ] `ClockDriftBanner` rendered.
- [ ] Modals: `UserProfileModal`, `MobileProfilePage`, `MobileTasksPage`, `CreateTeamModal`, `SettingsModal`, `CreateCollectionModal`, `CreateProjectModal`, `PaymentModal` (conditional on `selectedPaymentPackage && selectedTeamId`).
- [ ] Team-from-URL sync effect (invalid `teamId` → redirect to personal/first team) intact.
- [ ] `<Outlet/>` renders routed content; `mainContentClass` scroll/padding semantics preserved for resources vs other views, detail vs non-detail.
- [ ] Mobile bottom `MobileTabBar` (Parser / Downloads[personal-only] / Resources / Tasks), collapsible-on-scroll, downloads & resources popups, full-screen scrim, hidden on detail pages — **unchanged**.
- [ ] Mobile workspace avatar button (top-left, opens MobileProfilePage), hidden on detail pages — **unchanged**.
- [ ] Sidebar collapse state persisted to `localStorage('sidebar-collapsed')`.

**TopBar (desktop, `hidden sm:flex`):**
- [ ] Controls L→R: LanguageSwitcher, Search (stub), Task Center (badge = active+uploading; auto-opens on upload), Notifications (unread badge), Approvals (count badge, 60s poll), Avatar menu.
- [ ] Task Center panel: active/history tabs, live uploads, flow cards, cancel/retry/clearCompleted, 1s elapsed clock, open-resource nav, `TaskDetailModal` (portaled, survives panel close).
- [ ] Avatar menu: name/email header, Profile→settings('personal'), Settings→settings('general'), Help (stub), Sign Out.
- [ ] One-panel-at-a-time; click-outside + Escape close.

**Sidebar / nav (desktop, `hidden sm:flex`):**
- [ ] Three modes: personal / team / project (correct items per mode — see inventory).
- [ ] Personal items: Parser, Resources, Projects, Todolist, Shared, Points, AI Library (gated by `isViewEnabled`/permissions).
- [ ] Team items: Resources, Projects, Todolist, Shared, AI Library + Management submenu (Members, Billing) gated by `member.view`.
- [ ] Project mode: Back, Files (All Files, Upload[perm]), Review (Comments, Status), Members, Activity, Project Settings[perm].
- [ ] WorkspaceSwitcher: expanded dropdown + collapsed avatar popup, team colors, Create Team, active ring, navigates on select.
- [ ] Collapse toggle (FloatingCollapseTab) + tooltips when collapsed; active-state highlight via `pathnameToView`.

---

## File Structure

- **Create** `frontend/utils/featureFlags.ts` — typed env-flag reader (`islandUI()`).
- **Create** `frontend/utils/featureFlags.test.ts` — flag parsing tests.
- **Create** `frontend/components/IslandShell.tsx` — the desktop island frame (composes TopBar + Sidebar + Outlet in islands).
- **Create** `frontend/components/IslandShell.test.tsx` — render/composition tests.
- **Modify** `frontend/index.css` — add `.island-frame` background-glow + frame/island helper classes (append after the `@layer components` btn block).
- **Modify** `frontend/components/TopBar.tsx` — add `island?: boolean` prop → full-width root + brand; default false = today's markup.
- **Modify** `frontend/components/Sidebar.tsx` — add `island?: boolean` + `iconRail?: boolean` props → in-card root + 54px detail rail; default false = today's markup.
- **Modify** `frontend/components/AppLayout.tsx` — branch desktop frame on `islandUI()`; reuse all existing children/modals.

---

### Task 1: Feature-flag util (TDD)

**Files:**
- Create: `frontend/utils/featureFlags.ts`
- Test: `frontend/utils/featureFlags.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/utils/featureFlags.test.ts
import { describe, it, expect, afterEach, vi } from 'vitest';
import { islandUI } from './featureFlags';

describe('featureFlags.islandUI', () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  it('is false when the env var is unset', () => {
    vi.stubEnv('VITE_FEATURE_ISLAND_UI', '');
    expect(islandUI()).toBe(false);
  });

  it('is true only for the literal string "true"', () => {
    vi.stubEnv('VITE_FEATURE_ISLAND_UI', 'true');
    expect(islandUI()).toBe(true);
  });

  it('is false for any other value', () => {
    vi.stubEnv('VITE_FEATURE_ISLAND_UI', '1');
    expect(islandUI()).toBe(false);
  });
});
```

- [ ] **Step 2: Run it — expect FAIL** (`islandUI` not defined)

```bash
npx vitest run utils/featureFlags.test.ts
```
Expected: FAIL — cannot find module / `islandUI` is not a function.

- [ ] **Step 3: Implement**

```ts
// frontend/utils/featureFlags.ts
// Island redesign v2 — frontend feature flags. Convention: VITE_FEATURE_<NAME>,
// default OFF, read as the literal string "true" (Vite inlines import.meta.env
// at build time, so this is statically analyzable and tree-shakeable).

/** Island app-shell layout (spec D1–D12). OFF → the classic fixed-sidebar frame. */
export function islandUI(): boolean {
  return import.meta.env.VITE_FEATURE_ISLAND_UI === 'true';
}
```

- [ ] **Step 4: Run it — expect PASS**

```bash
npx vitest run utils/featureFlags.test.ts
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/utils/featureFlags.ts frontend/utils/featureFlags.test.ts
git commit -m "feat(shell): VITE_FEATURE_ISLAND_UI flag util (v2 P1b Task 1)"
```

---

### Task 2: Island frame CSS (glow + island card helpers)

**Files:**
- Modify: `frontend/index.css` (append a new block after the `@layer components { .btn-tint-* }` block, before `/* Global styles */`)

- [ ] **Step 1: Append the island-frame styles**

```css
/* ── Island app shell — island redesign v2 P1b (docs/design/island-redesign-spec.md §2).
   Frame = background micro-glow + flex column (topbar row + content row). The
   `.island` card chrome reuses the semantic tokens that already flip per theme,
   so the shell is dual-theme for free. Desktop-only; mobile keeps the flat frame. */
.island-frame {
  height: 100dvh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 12px 12px;
  background:
    radial-gradient(900px 500px at 75% -10%, rgba(99, 102, 241, .07), transparent 60%),
    radial-gradient(700px 400px at -10% 110%, rgba(99, 102, 241, .05), transparent 60%),
    var(--app-bg);
}
.island-frame__row {
  display: flex;
  gap: 12px;
  flex: 1;
  min-height: 0;
}
.island-card {
  background: var(--island);
  border: 1px solid var(--line);
  border-radius: var(--r-lg, 18px);
  box-shadow: var(--shadow-island, 0 10px 40px rgba(0, 0, 0, .45));
}
```

Note: `--r-lg` / `--shadow-island` are referenced with fallbacks because the P1a token block defines `--island/--line/--app-bg` but not the geometry tokens; the fallbacks match spec §1 exactly. (If you add `--r-lg`/`--shadow-island` to the `:root` token block, the fallbacks become inert — optional.)

- [ ] **Step 2: Verify the build still compiles & class is emitted**

```bash
npm run build 2>&1 | tail -1          # expect: ✓ built
```

- [ ] **Step 3: tsc + vitest baseline unchanged**

```bash
npx tsc --noEmit 2>&1 | wc -l         # expect 140
npx vitest run 2>&1 | grep "Tests "   # expect 932 passed + the 3 new from Task 1 = 935
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.css
git commit -m "feat(shell): island frame + glow CSS helpers (v2 P1b Task 2)"
```

---

### Task 3: TopBar `island` mode (full-width + brand)

**Files:**
- Modify: `frontend/components/TopBar.tsx`

The header today is: `fixed top-0 right-0 left-0 sm:left-{20|64} h-14 ... hidden sm:flex justify-end ...`. In island mode it must be a **full-width static strip with a brand on the left** (the shell positions it; it is no longer `fixed`/offset).

- [ ] **Step 1: Extend the props type**

In `interface TopBarProps` add:
```ts
  /** Render inside the island shell: static full-width strip with brand on the left. */
  island?: boolean;
```
Destructure with a default in the component signature: `}) => {` → add `island = false` alongside `sidebarCollapsed = false`.

- [ ] **Step 2: Add the brand element (island only)**

Import `Sparkles` from lucide-react (add to the existing import block). Inside the `<header>`, as the FIRST child (before the LanguageSwitcher block), add:
```tsx
      {island && (
        <div className="flex items-center gap-2 font-bold text-sm text-ink-100 pl-1 pr-2 select-none">
          <span className="w-6 h-6 rounded-[7px] grid place-items-center bg-gradient-to-br from-indigo-400 to-indigo-500 shadow-[0_0_16px_rgba(99,102,241,0.4)]">
            <Sparkles size={13} className="text-white" />
          </span>
          <span>MediaHub</span>
        </div>
      )}
```
(The existing controls are right-aligned by `justify-*`; see Step 3. Brand on the left + `grow` spacer keeps controls on the right.)

- [ ] **Step 3: Swap the root `<header>` className by mode**

Replace the single template-literal className on `<header>` with a mode switch. Keep the **flag-OFF string byte-identical** to today's:
```tsx
    <header
      className={
        island
          ? 'relative h-12 z-50 hidden sm:flex items-center gap-1.5 sm:gap-2 px-1'
          : `fixed top-0 right-0 left-0 ${sidebarCollapsed ? 'sm:left-20' : 'sm:left-64'} h-14 border-b border-ink-800 bg-ink-950/80 backdrop-blur-sm z-50 hidden sm:flex items-center justify-end px-3 sm:px-6 gap-1.5 sm:gap-2 transition-[left] duration-300`
      }
    >
```
Then, so controls stay right-aligned in island mode, insert a spacer right after the brand block (island only):
```tsx
      {island && <div className="flex-1" />}
```
Rationale: island topbar is NOT a bordered/fixed bar — it's the spec's "global zone" strip (`padding:2px 4px`, no border, no own background — the glow shows through). `h-12` ≈ mockup topbar height. The shell's frame provides spacing.

- [ ] **Step 4: tsc + render check**

```bash
npx tsc --noEmit 2>&1 | wc -l   # expect 140
npx vitest run 2>&1 | grep "Tests "   # expect unchanged (935)
```
(No new behavior to unit-test here; visual verified in Task 6. Existing TopBar tests, if any, must still pass.)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TopBar.tsx
git commit -m "feat(shell): TopBar island mode — full-width strip + brand (v2 P1b Task 3)"
```

---

### Task 4: Sidebar `island` + `iconRail` modes (in-card + 54px detail rail)

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

Today each mode's root `<aside>` is `fixed top-0 left-0 h-full w-{20|64} ... border-r border-ink-800 bg-ink-950 ... hidden sm:flex`. In island mode the shell draws the card, so the aside becomes `relative w-full h-full` (no fixed, no own border/bg/shadow — those are on the `.island-card` wrapper), and the `FloatingCollapseTab` stays. `iconRail` (detail pages, spec D5) forces the narrow 54px icon-only rail regardless of the user's collapse pref.

- [ ] **Step 1: Extend props**

In `SidebarProps` add:
```ts
  /** Render inside the island shell (static, fills the nav-island card). */
  island?: boolean;
  /** Force the 54px icon rail (detail routes, spec D5) — overrides `collapsed`. */
  iconRail?: boolean;
```
Destructure `island = false, iconRail = false` in the component.

- [ ] **Step 2: Compute effective collapse + root class once**

Near the top of the Sidebar render body (before the mode branches), add:
```tsx
  // In the island shell the card chrome (bg/border/radius/shadow) is drawn by
  // the shell wrapper, so the aside is a plain fill. iconRail (detail pages,
  // D5) pins the narrow rail; otherwise honor the user's collapse pref.
  const effCollapsed = iconRail || collapsed;
  const railWidth = iconRail ? 'w-[54px]' : effCollapsed ? 'w-20' : 'w-64';
  const asideBase = island
    ? `relative h-full ${railWidth} ${iconRail ? 'px-1.5 py-3' : effCollapsed ? 'p-3' : 'p-6'} flex flex-col`
    : null; // null → keep each mode's existing className verbatim
```
Then in **each** of the three mode branches, change the root `<aside className="...">` to:
```tsx
      <aside className={asideBase ?? "<EXISTING_CLASSNAME_VERBATIM>"}>
```
where `<EXISTING_CLASSNAME_VERBATIM>` is that branch's current class string (unchanged — flag-OFF must be byte-identical). Pass `collapsed={effCollapsed}` to every `SidebarItem`/`WorkspaceSwitcher`/logo conditional inside (replace local `collapsed` reads with `effCollapsed`) so the 54px rail renders icon-only.

> Implementation note: the simplest mechanical change is to introduce `const c = effCollapsed;` and use `c` wherever the branch currently reads `collapsed` for *rendering* (icon size, label hiding, justify-center, logo size, WorkspaceSwitcher `collapsed` prop). Do NOT change the `onToggleCollapse` wiring.

- [ ] **Step 3: FloatingCollapseTab in island mode**

The tab is `absolute -right-10 bottom-8`. Inside an island card with `overflow` visible that still works, but to avoid it colliding with the 12px gap, in island mode anchor it to the card's right edge: change its wrapper to `-right-3` when `island`. Keep icon logic. When `iconRail` is on (detail page), HIDE the collapse tab (the rail is intentionally pinned): render the tab only when `!iconRail`.

- [ ] **Step 4: tsc + vitest**

```bash
npx tsc --noEmit 2>&1 | wc -l   # expect 140
npx vitest run 2>&1 | grep "Tests "   # expect unchanged
```

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Sidebar.tsx
git commit -m "feat(shell): Sidebar island mode + 54px detail icon rail (v2 P1b Task 4)"
```

---

### Task 5: `IslandShell.tsx` frame component (TDD render)

**Files:**
- Create: `frontend/components/IslandShell.tsx`
- Test: `frontend/components/IslandShell.test.tsx`

This composes the desktop frame: glow background → topbar row (`<TopBar island/>`) → content row (`<Sidebar island/>` nav card + work card with `children`). It receives the SAME props AppLayout passes to TopBar/Sidebar today, plus `isDetailPage`. The work-island wraps `children` (the existing `<main><Outlet/></main>` content) unchanged.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/IslandShell.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import React from 'react';

// Stub the heavy children so the shell renders in isolation.
vi.mock('./TopBar', () => ({ TopBar: (p: any) => <div data-testid="topbar" data-island={String(p.island)} /> }));
vi.mock('./Sidebar', () => ({ Sidebar: (p: any) => <div data-testid="sidebar" data-island={String(p.island)} data-iconrail={String(p.iconRail)} /> }));

import { IslandShell } from './IslandShell';

const baseProps: any = {
  isDetailPage: false,
  topBarProps: { user: null, onSignOut: () => {} },
  sidebarProps: { mode: 'personal', settingsTab: '', teams: [], activeTeamId: null, personalTeamId: null, currentTeam: null, permissions: [], activeProject: null, isLibraryOpen: false, isSettingsOpen: false, activeSmartCollectionId: null, onSettingsTabChange: () => {}, onToggleLibrary: () => {}, onToggleSettings: () => {}, onTeamChange: () => {}, onCreateTeam: () => {}, onProjectBack: () => {}, onProjectSelect: () => {} },
};

describe('IslandShell', () => {
  it('renders topbar + sidebar in island mode and the routed children', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps}><div data-testid="content">hi</div></IslandShell>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('topbar').dataset.island).toBe('true');
    expect(screen.getByTestId('sidebar').dataset.island).toBe('true');
    expect(screen.getByTestId('sidebar').dataset.iconrail).toBe('false');
    expect(screen.getByTestId('content')).toBeTruthy();
  });

  it('forces the icon rail on detail pages (spec D5)', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps} isDetailPage><div /></IslandShell>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('sidebar').dataset.iconrail).toBe('true');
  });
});
```

- [ ] **Step 2: Run — expect FAIL** (module missing)

```bash
npx vitest run components/IslandShell.test.tsx
```

- [ ] **Step 3: Implement**

```tsx
// frontend/components/IslandShell.tsx
import React from 'react';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';

// Island redesign v2 P1b — desktop app shell. Background micro-glow + a
// full-width global topbar strip, then a content row with a floating nav
// island and a workspace island. The heavy children (TopBar/Sidebar) keep all
// their logic; we only place them inside island cards and pass `island`. Detail
// routes pin the 54px icon rail (spec D5). Mobile chrome is rendered by
// AppLayout outside this shell and is unaffected.
interface IslandShellProps {
  isDetailPage: boolean;
  topBarProps: React.ComponentProps<typeof TopBar>;
  sidebarProps: React.ComponentProps<typeof Sidebar>;
  children: React.ReactNode;
}

export function IslandShell({ isDetailPage, topBarProps, sidebarProps, children }: IslandShellProps) {
  return (
    <div className="island-frame hidden sm:flex">
      <TopBar {...topBarProps} island />
      <div className="island-frame__row">
        {/* Nav island */}
        <div className="island-card relative flex-shrink-0">
          <Sidebar {...sidebarProps} island iconRail={isDetailPage} />
        </div>
        {/* Workspace island — wraps today's routed content unchanged */}
        <main className="island-card flex-1 min-w-0 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS**

```bash
npx vitest run components/IslandShell.test.tsx   # 2 passed
```

- [ ] **Step 5: Commit**

```bash
git add frontend/components/IslandShell.tsx frontend/components/IslandShell.test.tsx
git commit -m "feat(shell): IslandShell frame component (v2 P1b Task 5)"
```

---

### Task 6: Wire AppLayout to switch frames by flag

**Files:**
- Modify: `frontend/components/AppLayout.tsx`

Keep the entire provider stack, all modals, mobile chrome, effects, and handlers exactly as they are. Only the **desktop frame** (Sidebar + TopBar + `<main>`) is chosen by `islandUI()`. Mobile pieces (`MobileTabBar`, mobile avatar button, `MobileProfilePage`, `MobileTasksPage`) render in BOTH modes — they are `sm:hidden`, and `IslandShell` is `hidden sm:flex`, so they never overlap.

- [ ] **Step 1: Import**

Add: `import { IslandShell } from './IslandShell';` and `import { islandUI } from '../utils/featureFlags';`

- [ ] **Step 2: Hoist the Sidebar/TopBar prop objects**

Today the `<Sidebar ... />` and `<TopBar ... />` JSX live directly in the return. Extract their props into objects so both frames share them verbatim:
```tsx
  const island = islandUI();

  const sidebarProps = {
    mode: sidebarMode, view, settingsTab, collapsed: sidebarCollapsed,
    onToggleCollapse: handleToggleSidebar,
    teams, activeTeamId: selectedTeamId, personalTeamId, currentTeam,
    permissions: userPermissions, isViewEnabled, userName: userProfile?.name,
    userRole: userProfile?.role, activeProject: selectedProject,
    isLibraryOpen, isSettingsOpen, activeSmartCollectionId,
    onViewChange: (v: string) => { if (v === 'mediatrack') { setSelectedProject(null); setReviewFile(null); } },
    onSettingsTabChange: (tab: string) => setSettingsTab(tab as any),
    onToggleLibrary: toggleLibraryMenu, onToggleSettings: toggleSettingsMenu,
    onTeamChange: () => { setActiveCollectionId(null); setSelectedProject(null); setReviewFile(null); },
    onCreateTeam: handleCreateTeam,
    onProjectBack: () => { setSelectedProject(null); setReviewFile(null); },
    onSmartCollectionSelect: (collection: any) => {
      setIsSearchActive(false); setSearchResults([]); setSearchQueryText('');
      setActiveSmartCollectionId(collection?.id || null);
      navigate(teamPath('/resources/downloads'));
      setActiveCollectionId(null); setActiveLibraryTab('my-library');
    },
    onProjectSelect: setSelectedProject,
  } as const;

  const topBarProps = {
    user: userProfile ? { name: userProfile.name, email: userProfile.email, avatarUrl: userProfile.avatarUrl } : null,
    unreadCount: notifications.filter(n => !n.read).length,
    onSignOut: handleAuthLogout,
    onOpenSettings: (tab?: string) => { setSettingsModalInitialTab(tab || 'personal'); setIsSettingsModalOpen(true); },
    sidebarCollapsed,
  } as const;
```
> Copy the prop values **exactly** from the current JSX (don't paraphrase) — the existing `<Sidebar .../>`/`<TopBar .../>` are the source of truth. If any handler differs from above, prefer what's in the file today.

- [ ] **Step 3: Branch the desktop frame**

Replace the current `<Sidebar .../>`, `<TopBar .../>`, and `<main className={mainContentClass}><Outlet/></main>` trio with:
```tsx
      {island ? (
        <IslandShell isDetailPage={isDetailPage} topBarProps={topBarProps} sidebarProps={sidebarProps}>
          <Outlet />
        </IslandShell>
      ) : (
        <>
          <Sidebar {...sidebarProps} />
          <TopBar {...topBarProps} />
          <main className={mainContentClass}>
            <Outlet />
          </main>
        </>
      )}
```
Keep the mobile avatar button, `MobileTabBar`, and all modals exactly where they are (siblings of this block). The outer wrapper `<div className="flex min-h-[100dvh] ...">` stays for the OFF path; in island mode `IslandShell` provides its own `h-100dvh` frame, and the outer flex div simply contains it (the `hidden sm:flex` on IslandShell + `sm:hidden` mobile pieces keep them mutually exclusive). The outer div's `bg-ink-950` is harmless behind the island frame.

- [ ] **Step 4: Full verification (flag OFF — must be a no-op)**

```bash
npx tsc --noEmit 2>&1 | wc -l        # expect 140
npx vitest run 2>&1 | grep "Tests "  # expect 932 + 3 (Task1) + 2 (Task5) = 937 passed
npm run build 2>&1 | tail -1         # ✓ built
bash scripts/check-no-zinc.sh        # no zinc utilities — OK
```

- [ ] **Step 5: Visual verification BOTH modes** (dev server; port from `.worktree.env` or whatever Vite picks)

```bash
# OFF (default): npm run dev → app frame must be pixel-identical to production.
# ON: VITE_FEATURE_ISLAND_UI=true npm run dev → island frame.
```
With the flag ON, verify against `docs/design/mockups/mediahub-resources-redesign-v2.html`:
- full-width topbar strip with brand on the left, controls on the right;
- floating nav island (198px) + workspace island with 12px gap + background glow;
- navigate to a detail route (`/resources/file/:id` or `/player/:id`) → nav island shrinks to 54px icon rail, nav still present (D5);
- run the **entire D12 checklist** above (open Task Center, Approvals, Avatar menu, switch workspace, collapse sidebar, open each nav item, open a modal) — all must work.
- toggle Light theme (Settings → Appearance) → island frame readable in light (tokens already flip).
Use the browser MCP + `document.documentElement` probes as in the P1a light-polish verification if screenshots don't persist.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/AppLayout.tsx
git commit -m "feat(shell): AppLayout switches island frame by VITE_FEATURE_ISLAND_UI (v2 P1b Task 6)"
```

---

### Task 7: Ship (flag default OFF in prod)

- [ ] **Step 1: Confirm prod default is OFF.** Do NOT set `VITE_FEATURE_ISLAND_UI` in Vercel yet — the flag stays OFF in production so this PR is a pure no-op for users; the island shell ships dark behind the flag and is enabled later (after P2–P5 page migrations) per spec §6.

- [ ] **Step 2: Version + PR.** Read `origin/master` `frontend/package.json` version, bump patch (avoid parallel-session collision). PR body: D12 statement (flag-gated, OFF path byte-identical, all 3 component inventories re-verified) + the standard ship chain (repo public → CI green → merge → confirm Vercel Production row + `curl` live version → inflight=0 → repo private). `gh pr merge` then `gh pr view` to confirm `MERGED`.

- [ ] **Step 3: Update memory** `project_island_redesign.md` progress item 4 → P1b shipped; note flag is OFF in prod, enable after P2–P5.

---

## Self-Review

**1. Spec coverage:**
- §2 layout (topbar full-width + nav island + work island + gaps + glow) → Tasks 2/3/5. ✅
- D5 (nav never disappears; 54px rail on detail) → Task 4 (`iconRail`) + Task 5 (`isDetailPage`→iconRail) + Task 6 visual. ✅
- D11 dual-theme → island card uses semantic tokens that flip (Task 2). ✅
- D4 no emoji → brand is a gradient square + lucide `Sparkles`. ✅
- D12 zero feature change → flag OFF byte-identical (Tasks 3/4 keep verbatim class strings; Task 6 OFF branch unchanged) + full parity checklist re-run (Task 6 Step 5). ✅
- Flag `VITE_FEATURE_ISLAND_UI` (memory `feature flag` convention `VITE_FEATURE_<NAME>` default false) → Task 1. ✅
- Splitter / resizable / detail-island / per-page rail-inside-work / Cmd+K / audio-tint / comet back → explicitly **out of scope** (P2–P4). ✅

**2. Placeholder scan:** No TBD/TODO. The one "copy verbatim" instruction (Task 4 Step 2 existing classnames, Task 6 Step 2 prop values) points at the live file as the source of truth rather than inventing values — intentional, since paraphrasing classes would break D12. Every new code block is complete.

**3. Type consistency:** `islandUI()` (Task 1) used in Task 6. `island?: boolean` added to both TopBar (Task 3) and Sidebar (Task 4) and consumed in IslandShell (Task 5) + AppLayout (Task 6). `iconRail?: boolean` defined in Task 4, passed in Task 5. `IslandShellProps` fields (`isDetailPage`, `topBarProps`, `sidebarProps`, `children`) match the Task 6 call site. `React.ComponentProps<typeof TopBar>` / `<typeof Sidebar>` keep the prop objects type-checked against the real components — if Task 3/4 prop additions are optional (`?`), the hoisted objects in Task 6 stay valid.

**4. Risks & mitigations:**
- *Sidebar mechanical `collapsed`→`effCollapsed` swap (Task 4)* is the riskiest edit (567L, 3 branches). Mitigation: change only **render** reads, never the toggle wiring; flag-OFF path keeps verbatim classnames so tsc/vitest/visual diff catch regressions. Consider doing Task 4 as the dedicated subagent task with a focused diff review.
- *Outer `<div className="flex min-h-[100dvh]">` + IslandShell's own `h-100dvh`*: IslandShell is `hidden sm:flex`; on desktop the outer flex contains a single full-height child — fine. Verify no double scrollbar in Task 6 Step 5.
- *TaskCenter/Approvals/Avatar panels use `fixed sm:absolute ... top-full`* (PanelShell): in island mode the topbar is `relative`, so `sm:absolute top-full` anchors to each control's wrapper (already `relative`) — unaffected. Confirm in Task 6 Step 5.
