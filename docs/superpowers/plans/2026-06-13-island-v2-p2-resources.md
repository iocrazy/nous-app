# Island Redesign v2 — P2: Resources Page (work island + separate info island)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** P1b shell (#694) merged; the user must have **dogfooded the P1b island shell ON** (logged-in visual pass) and signed off before starting P2 — P2 builds directly on the IslandShell frame. The `VITE_FEATURE_ISLAND_UI` flag remains **OFF in prod** through P2 (enabled only after P2–P5 per spec §6).

**Goal:** Render the resources/library page correctly inside the island work area, and promote its info panel from a `fixed` overlay to a **separate info island** (right of the work island, with a draggable splitter + collapse/reopen) — matching `docs/design/mockups/mediahub-resources-redesign-v2.html`, with zero feature change (D12).

**Architecture (decision: separate info island — user-chosen):** `IslandShell` gains a **right-island slot**. It owns the content-row flex math (`nav island | work island | splitter | info island`), the splitter drag → info-island width (clamp 250–480, spec §2), and visibility. A new `IslandWorkContext` exposes `{ infoIslandRef, infoVisible, setInfoVisible, infoWidth }`. The resources page, in island mode, **React-portals** its info-panel content (FolderInfoPanel/ResourceInfoPanel) into `infoIslandRef` and drives `setInfoVisible` from `ResourcesContext.showInfoPanel` — so the shell renders the floating island chrome while the page keeps owning the panel's content/logic. `ResourcesShell` drops its classic negative-margin/`fixed`-overlay layout in island mode and fills the work island with `[rail | content]`. Everything stays behind `islandUI()`; the classic path is byte-identical (D12).

**Tech Stack:** React 19 + TS, react-dom `createPortal`, Tailwind v4 (island semantic tokens already in `index.css`), `IslandShell` from P1b, vitest + @testing-library/react.

**Hard constraints (spec):**
- **D12 zero feature change** — every resources interaction in the checklist below works identically; flag-OFF renders today's DOM.
- **D7** info panel draggable-width + collapsible; **density not reduced** (keep colored tag chips, icon rows, all Properties).
- **D3** sidebar groups + management pinned bottom (already done by v1 — must survive).
- **D4** no emoji (the mockup's emoji are placeholders; use existing lucide icons/type badges).
- tsc baseline (currently **126** lines after P1b) must not increase; vitest stays green (currently **941**); new code uses `ink-*`/semantic tokens only (no `*-zinc-*`, CI guard).
- Commit each task; flag stays OFF in prod.

**Out of scope:** P3 video / P4 audio detail pages; Cmd+K search; any backend/data change; mobile redesign (mobile resources keeps today's layout — island frame is `hidden sm:flex`). The grid/cards visual polish is **only** what's needed to match the mockup density (hover lift, selected glow); no new filtering/sorting features.

---

## D12 Feature-Parity Checklist (acceptance baseline — re-verify with flag ON)

Resources page inventory (entry: `pages/ResourcesPage.tsx` → `ResourcesView` → `ResourcesViewInner` → `ResourcesShell`[`ResourcesSidebar` | `ResourceGrid` | `ResourcesInfoPanelWrapper`], state in `contexts/ResourcesContext.tsx`).

- [ ] **Secondary rail** (`ResourcesSidebar`): Locations (My Downloads/My Uploads/Temporary), Libraries (collapsible, team scope), Smart Folders (collapsible, `+` add), Project Assets, **Manage pinned bottom** (Share Manager, Recycle Bin) + divider (D3); collapse toggle; counts.
- [ ] **Content header** (`ResourceGrid` toolbar): breadcrumb/title, `ToolbarSearch` (+ AI hybrid/semantic + scope picker), filter-bar visibility toggle, sort dropdown (newest/oldest/name/size), view-mode cycle (grid→justified→list), Upload (file/folder), New (folder/smart-folder/fetch-URL).
- [ ] **Filter row**: desktop `FilterBar` chips (Type All/Images/Video/Audio/Docs, Tags, Rating, Date, Duration, Aspect, AI, Source, social), server-side `filterParams`; mobile `FilterChipBar` + `FacetPickerSheet`.
- [ ] **Folders + files**: `FolderCard` (preview grid, rename, ⋮ menu, drop target, checkbox), `ResourceCard` (thumb/icon, rename, size/date, trash/restore/delete, ⋮, checkbox, transcoding spinner, temp TTL badge); grid/justified/list modes; group headers w/ counts; infinite-scroll sentinel; empty states.
- [ ] **Selection**: hover checkbox, multi-select mode, Select-All/Cancel bar, `BatchSelectionToolbar` (bottom-center pill: Move/Copy/Describe/Auto-tag/Training-Set/Delete; recycle: Restore/Permanent-Delete).
- [ ] **Info panel** (`ResourcesInfoPanelWrapper` → `ResourceInfoPanel`/`FolderInfoPanel`): cover/icon, click-to-edit filename, **+ Add note / + Add source link** rows, Properties (rating, size, type, resolution, source, created/modified, folder), tags (colored chips + add), AI status, rating stars; resizable 250–480; collapse `»` + right-edge reopen; recycle read-only.
- [ ] **Context menus**, drag-drop upload overlay, modals (`ResourcesModals`, `SmartFolderEditor`, `FolderPickerModal`, `ResourceFetchUrlModal`).
- [ ] **Scope**: personal vs team; downloads/shared/recycle/library/smart sub-routes.

---

## File Structure

- **Create** `frontend/contexts/IslandWorkContext.tsx` — context exposing the right-island portal ref + visibility/width; `useIslandWork()` hook (returns a no-op/inert shape when not inside an island shell, so callers are safe in classic mode).
- **Create** `frontend/contexts/IslandWorkContext.test.tsx` — default/inert + provider value tests.
- **Modify** `frontend/components/IslandShell.tsx` — render work island + optional splitter + info island (portal target); own width/visibility state; wrap children in `IslandWorkProvider`. Add a reopen handle when info is collapsed.
- **Modify** `frontend/components/ResourcesShell.tsx` — island-mode branch: drop negative-margins/`fixed` overlay; `[rail | content]` fills the work island; portal the info panel into the shell's info island; sync `showInfoPanel`↔`setInfoVisible`.
- **Modify** `frontend/components/ResourcesInfoPanelWrapper.tsx` — add `island?` mode that renders ONLY the inner panel content (no `fixed` wrapper / own resize / own collapse — the shell owns those) for portaling.
- **Modify** `frontend/components/ResourceGrid.tsx` + `frontend/components/ResourceCard.tsx` + `frontend/components/FolderCard.tsx` — visual-only polish to mockup density (hover lift, selected indigo glow) **iff** not already present from v1; verify first, change minimally.
- **Modify** `frontend/index.css` — add `.island-info` / `.island-splitter` helpers if needed (reuse `.island-card`).

---

### Task 1: `IslandWorkContext` (TDD)

**Files:** Create `frontend/contexts/IslandWorkContext.tsx` + `.test.tsx`.

- [ ] **Step 1: failing test**

```tsx
// IslandWorkContext.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React, { useRef } from 'react';
import { IslandWorkProvider, useIslandWork } from './IslandWorkContext';

function Probe() {
  const { infoVisible, setInfoVisible, infoWidth } = useIslandWork();
  return (
    <div>
      <span data-testid="vis">{String(infoVisible)}</span>
      <span data-testid="w">{infoWidth}</span>
      <button onClick={() => setInfoVisible(true)}>show</button>
    </div>
  );
}

describe('IslandWorkContext', () => {
  it('inert defaults when no provider (classic mode safe)', () => {
    render(<Probe />);
    expect(screen.getByTestId('vis').textContent).toBe('false');
  });
  it('provider tracks visibility', () => {
    render(<IslandWorkProvider><Probe /></IslandWorkProvider>);
    fireEvent.click(screen.getByText('show'));
    expect(screen.getByTestId('vis').textContent).toBe('true');
  });
});
```

- [ ] **Step 2: run → FAIL** (`npx vitest run contexts/IslandWorkContext.test.tsx`)

- [ ] **Step 3: implement**

```tsx
// IslandWorkContext.tsx
import React, { createContext, useContext, useMemo, useRef, useState } from 'react';

interface IslandWorkValue {
  /** Portal target for a page-provided right info island (null in classic mode). */
  infoIslandRef: React.RefObject<HTMLDivElement | null>;
  infoVisible: boolean;
  setInfoVisible: (v: boolean) => void;
  infoWidth: number;            // clamped 250–480 (spec §2)
  setInfoWidth: (w: number) => void;
  /** True only inside an island shell — pages branch on this. */
  active: boolean;
}

const INERT: IslandWorkValue = {
  infoIslandRef: { current: null },
  infoVisible: false,
  setInfoVisible: () => {},
  infoWidth: 360,
  setInfoWidth: () => {},
  active: false,
};

const Ctx = createContext<IslandWorkValue>(INERT);

export const IslandWorkProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const infoIslandRef = useRef<HTMLDivElement | null>(null);
  const [infoVisible, setInfoVisible] = useState(false);
  const [infoWidth, setInfoWidthState] = useState(360);
  const setInfoWidth = (w: number) => setInfoWidthState(Math.min(480, Math.max(250, w)));
  const value = useMemo(
    () => ({ infoIslandRef, infoVisible, setInfoVisible, infoWidth, setInfoWidth, active: true }),
    [infoVisible, infoWidth],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

export function useIslandWork(): IslandWorkValue {
  return useContext(Ctx);
}
```

- [ ] **Step 4: run → PASS**; tsc unchanged (126).
- [ ] **Step 5: commit** `feat(shell): IslandWorkContext for page-provided info island (v2 P2 Task 1)`

---

### Task 2: IslandShell renders the info island + splitter

**Files:** Modify `frontend/components/IslandShell.tsx`, `frontend/index.css`.

- [ ] **Step 1:** Wrap the shell body in `<IslandWorkProvider>` and consume it via an inner component (so the splitter/island can read context). Render, in the content row, after the work island:

```tsx
// inside IslandShell, content row — consume context in an inner child:
{infoVisible && (
  <>
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={startDrag}   // pointer-capture drag → setInfoWidth(window.innerWidth - clientX - 12)
      className="w-3 shrink-0 cursor-col-resize flex items-center justify-center group"
    >
      <span className="w-1 h-11 rounded bg-line-strong group-hover:bg-[rgb(var(--indigo))] transition-colors" />
    </div>
    <aside className="island-card shrink-0 overflow-auto" style={{ width: infoWidth }}>
      <div ref={infoIslandRef} className="h-full" />  {/* portal target */}
    </aside>
  </>
)}
{!infoVisible && hasInfoPage && (
  <button className="island-reopen" onClick={() => setInfoVisible(true)}>‹ Info</button>
)}
```

Width math mirrors the mockup (`Math.min(480, Math.max(250, window.innerWidth - clientX - 12))`). Work island stays `flex-1 min-w-0`.

- [ ] **Step 2:** `index.css` — add `.island-reopen` (fixed right-edge vertical tab, `island-card`-like) and `.island-splitter` if the inline classes get unwieldy. Reuse `--line-strong` / `rgb(var(--indigo))`.
- [ ] **Step 3:** Update `IslandShell.test.tsx` — assert the info island + splitter render when `infoVisible` (drive via a test child that calls `setInfoVisible(true)`), and are absent otherwise. Keep the existing 2 tests green.
- [ ] **Step 4:** tsc 126, vitest green, build OK.
- [ ] **Step 5:** commit `feat(shell): IslandShell info island slot + splitter (v2 P2 Task 2)`

---

### Task 3: ResourcesShell island-mode layout + info portal

**Files:** Modify `frontend/components/ResourcesShell.tsx`, `frontend/components/ResourcesInfoPanelWrapper.tsx`.

- [ ] **Step 1:** `ResourcesInfoPanelWrapper` — add `island?: boolean`. When `island`, render ONLY the inner content (the existing `FolderInfoPanel`/`ResourceInfoPanel` switch) without the `fixed top-14 right-0` wrapper, without its own resize handle, and without its own collapse tab (the shell owns width + collapse now). Classic path unchanged (byte-identical).
- [ ] **Step 2:** `ResourcesShell` — branch on `islandUI()`:
  - Classic: today's `<div className="flex min-h-screen sm:h-full sm:min-h-0 sm:-m-8 sm:-mt-20 sm:-mb-8">[Sidebar][content][InfoPanelWrapper]</div>` — unchanged.
  - Island: `<div className="flex h-full min-h-0">[ResourcesSidebar][content flex-1 min-w-0]</div>` (no negative margins — the work island has no AppLayout padding to escape; no `sm:pt-14` — the global topbar is outside the island). Then `createPortal(<ResourcesInfoPanelWrapper island {...infoPanelProps} />, infoIslandRef.current)` when `infoIslandRef.current` exists.
  - Sync: `useEffect(() => setInfoVisible(showInfoPanel && hasSelection), [showInfoPanel, hasSelection])` (read `showInfoPanel`/selection from `ResourcesContext`); when the shell's reopen sets `infoVisible` true, reflect into `ResourcesContext.setShowInfoPanel(true)` so existing logic stays the source of truth. Wire the collapse `»` (in the panel content) to `setShowInfoPanel(false)` (already its behavior) → effect hides the island.
- [ ] **Step 3:** Verify the rail still renders inside the work island (`ResourcesSidebar` is `hidden md:flex` — keep; in island mode it's a column inside the work card). Confirm `ResourceGrid`'s `md:h-full`/internal scroll still works inside the work island (it should — the work island is `overflow-auto` and the grid manages its own scroll region).
- [ ] **Step 4:** tsc 126, vitest green, build OK, no-zinc OK.
- [ ] **Step 5:** commit `feat(resources): island layout — rail+content in work island, info portaled to info island (v2 P2 Task 3)`

---

### Task 4: Card / grid visual polish to mockup density

**Files:** Modify (minimally, after verifying current state) `frontend/components/ResourceCard.tsx`, `frontend/components/FolderCard.tsx`, `frontend/components/ResourceGrid.tsx`.

- [ ] **Step 1:** Verify what v1 (#689) already did (hover lift, selected indigo ring/glow, folder tab inside card). `git log -p --follow` + read current cards. Only add what's missing vs mockup: card `hover:-translate-y-0.5 + shadow`, selected `ring-1 ring-[rgb(var(--indigo))] + glow`, segmented type filter pill styling. Do NOT change selection/nav/menu behavior (D12).
- [ ] **Step 2:** Ensure all polish uses semantic tokens; no zinc.
- [ ] **Step 3:** tsc/vitest/build/no-zinc green.
- [ ] **Step 4:** commit `feat(resources): card hover lift + selected glow to mockup density (v2 P2 Task 4)`

---

### Task 5: Verify + ship (flag still OFF in prod)

- [ ] **Step 1:** Full verification — tsc 126, vitest green, `npm run build` (OFF & `VITE_FEATURE_ISLAND_UI=true`), `scripts/check-no-zinc.sh`.
- [ ] **Step 2:** Visual dogfood (REQUIRES the user / an authenticated session): `VITE_FEATURE_ISLAND_UI=true npm run dev`, log in, open Resources. Run the entire D12 checklist; verify the info island appears on selection, splitter drags 250–480, `»` collapses + `‹ Info` reopens, light theme readable, classic mode (flag OFF) pixel-identical. **This page cannot be visually verified headless without credentials — the user must sign off.**
- [ ] **Step 3:** Version bump (read `origin/master` first), PR with D12 statement + standard ship chain (public → CI → merge → Vercel Production row + curl live version → inflight=0 → private). `gh pr merge` then `gh pr view` MERGED. Flag stays OFF in prod.
- [ ] **Step 4:** Update memory `project_island_redesign.md` item 5 → P2 shipped (flag still OFF; enable after P5).

---

## Self-Review

**1. Spec coverage:** §5.1 rail (already v1 — preserved via checklist), content header, filter row, folder/file cards, info panel as **separate island** (user-chosen — Tasks 1–3), splitter 250–480 (Task 2), card hover/selected glow + density (Task 4), D3 manage-bottom (preserved), D7 resizable+collapsible (shell-owned), D4 no-emoji (use existing icons), D12 (flag-OFF byte-identical + full checklist). ✅
**2. Placeholder scan:** Task 4 says "verify then minimally change" rather than dictating diffs because v1 may already cover it — the verify step is explicit, not a TODO. All new code (context, shell slot, portal wiring) is concrete. No TBD.
**3. Type consistency:** `useIslandWork()` shape (`infoIslandRef`/`infoVisible`/`setInfoVisible`/`infoWidth`/`setInfoWidth`/`active`) defined in Task 1, consumed in Tasks 2 (shell) & 3 (resources). `ResourcesInfoPanelWrapper` `island?` added Task 3 Step 1, used Step 2. Portal target ref flows shell→context→page.
**4. Risks:**
- *Portal timing*: `infoIslandRef.current` is null on first render (ref attaches after the info island mounts, which depends on `infoVisible`). Mitigate: the resources effect sets `infoVisible` first; portal renders on the next commit when the ref is attached (gate the `createPortal` on `infoIslandRef.current` truthiness + a re-render trigger, e.g. a `useState` mirror of the ref or `useIslandWork`'s `infoVisible` as the dependency). Spell this out during execution; add a render-after-attach guard.
- *Scroll/height*: the work island is `overflow-auto`; `ResourceGrid` also scrolls internally. Verify no double scrollbar (Task 3 Step 3 / dogfood).
- *Classic path*: every change is `islandUI()`-gated or `island?`-prop-gated; flag-OFF must stay byte-identical — diff-review each file's OFF branch.
- *Un-verifiable headless*: the whole page depends on auth; the user's dogfood (Task 5 Step 2) is the real gate before enabling the flag. Ship stays flag-OFF regardless.
