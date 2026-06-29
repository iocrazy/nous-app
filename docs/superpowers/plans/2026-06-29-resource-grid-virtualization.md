# Resource Grid Virtualization (grid mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound the DOM node count of the resource library's **grid** view (the default mode) so a single user can browse a 100k+ item library smoothly — by row-virtualizing the keyset-paginated grid with `@tanstack/react-virtual` instead of rendering every accumulated item.

**Architecture:** Today `ResourceGrid.tsx` renders `sortedItems.map(...)` over ALL accumulated items (keyset pagination appends unbounded; no windowing lib). At ~15k items the DOM/memory/decode cost kills the browser. This plan virtualizes the **grid-mode** rendering of the resources keyset path: a row virtualizer over the existing scroll container (`contentScrollRef`) renders only visible rows, each row a fixed-column grid of `ResourceCard`s. Column count is derived from the container width (mobile 2; desktop auto-fill ~160–220px cards). The folder section (bounded) and the existing `loadMoreRef` infinite-scroll sentinel stay intact. **Scope v1 = grid mode only** (the default); `list` and `justified` (masonry) modes are deferred to a follow-up (documented), so this ships a contained, low-regression increment.

**Tech Stack:** React 19 + Vite + TailwindCSS + `@tanstack/react-virtual` (new dep, React-19-compatible). Frontend: `cd frontend && npm run build` + `npm test` (vitest).

## Global Constraints

- **No behavior change except DOM windowing.** Selection, touch handlers, badges (`ttlBadgeText`), per-item keys, click/open, drag, and the keyset `loadMore` sentinel must behave identically. Only off-screen rows are unmounted.
- **Scope = grid mode of the resources keyset path only.** Do NOT touch `list` or `justified` rendering, the folder/section rendering, the Downloads-with-folders path, or any non-`isResourcesView`/`isRecycleView` block in v1. If unsure which block is the keyset path, it is the one whose bottom sentinel is `loadMoreRef` (gated `(isResourcesView || isRecycleView) && (hasMore || isLoadingMore)`).
- **The scroll element is the existing `contentScrollRef`** (the `flex-1 ... overflow-y-auto` container, ResourceGrid.tsx:819). Do NOT introduce a new nested scroll container — the virtualizer reads `getScrollElement: () => contentScrollRef.current`.
- **Column count is OUR value, not CSS auto-fill.** When virtualizing we render each row with an explicit `grid-template-columns: repeat(${columns}, minmax(0,1fr))`; the CSS `.downloads-grid` auto-fill no longer governs the virtualized rows. Column count from container width: mobile (<768px) = 2; desktop = `clamp` of `floor(width / CARD_SLOT)` where `CARD_SLOT ≈ 172` (≈160px min card + 12px gap), min 2, max 8. Recompute on container resize (ResizeObserver).
- **Variable row height** — cards have variable height (thumbnail + title/meta). Use the virtualizer's dynamic measurement (`measureElement`) with an `estimateSize` ≈ 280px; never assume a fixed row height.
- **Keys** stay `item.id` (per-card); rows keyed by `virtualRow.key`.
- **Graceful + progressive** — when `sortedItems.length` is small the virtualizer still works (renders all rows); no special-case threshold needed. Loading skeleton / empty states unchanged.
- UI copy unchanged (no new strings).

---

### Task 1: Add the dep + a reusable grid virtualization hook

**Files:**
- Modify: `frontend/package.json` (+ `@tanstack/react-virtual`)
- Create: `frontend/hooks/useGridVirtualizer.ts`
- Create: `frontend/utils/gridColumns.ts` (pure column-count helper)
- Test: `frontend/utils/gridColumns.test.ts`

**Interfaces:**
- Produces:
  - `columnsForWidth(width: number, isMobile: boolean): number` (pure) — `isMobile` → 2; else `Math.min(8, Math.max(2, Math.floor(width / 172)))`; `width<=0` → returns 2 (safe default before first measure).
  - `useGridVirtualizer(opts: { scrollRef: React.RefObject<HTMLElement>, itemCount: number, estimateRowHeight?: number }): { columns: number, rowVirtualizer: Virtualizer, containerRef: React.RefObject<HTMLDivElement>, totalSize: number }` — measures the container width via a ResizeObserver on `containerRef` (the inner content div) to derive `columns` (via `columnsForWidth`, with `isMobile = width < 768` OR `window.innerWidth < 768` — pick container width < 768), creates a `useVirtualizer` of `Math.ceil(itemCount / columns)` rows over `scrollRef`, with `estimateSize: () => estimateRowHeight ?? 280`, `overscan: 3`, `measureElement` enabled. Recreates/reacts when `columns` or `itemCount` change.

- [ ] **Step 1: Install** `cd frontend && npm install @tanstack/react-virtual` (confirm a React-19-compatible version resolves; it's peer-compatible). Verify `package.json` + lockfile updated.

- [ ] **Step 2: Write the failing test** (`gridColumns.test.ts`): `columnsForWidth(0,false)===2`; `columnsForWidth(375,true)===2`; `columnsForWidth(800,false)===Math.floor(800/172)` (=4); `columnsForWidth(2000,false)===8` (clamped); `columnsForWidth(300,false)===2` (min clamp).

- [ ] **Step 3: Run test, verify it fails.** `cd frontend && npm test -- gridColumns`

- [ ] **Step 4: Implement** `gridColumns.ts` (pure) + `useGridVirtualizer.ts`. The hook: a `containerRef` for the inner content wrapper (full width), a `ResizeObserver` updating a `width` state, `columns = columnsForWidth(width, width>0 && width<768)`, `rowCount = Math.ceil(itemCount / columns)`, `const rowVirtualizer = useVirtualizer({ count: rowCount, getScrollElement: () => scrollRef.current, estimateSize: () => estimateRowHeight ?? 280, overscan: 3 })`. Return `{ columns, rowVirtualizer, containerRef, totalSize: rowVirtualizer.getTotalSize() }`. (Import `useVirtualizer` from `@tanstack/react-virtual`.)

- [ ] **Step 5: Run test, verify pass.** Typecheck: `cd frontend && npm run build` green (the hook compiles; it's not wired yet). **Commit:** `feat(frontend): grid virtualization hook + column helper`

---

### Task 2: Virtualize the grid-mode resources render

**Files:**
- Modify: `frontend/components/ResourceGrid.tsx` (grid-mode branch of the resources keyset path)
- Test: `frontend/components/ResourceGrid.virtualization.test.tsx` (new)

**Interfaces:**
- Consumes: `useGridVirtualizer` (Task 1).

- [ ] **Step 1: Read** `ResourceGrid.tsx` carefully. Identify the resources keyset path = the conditional block that (a) renders the three `sortedItems.map` view-mode branches (`justified` / `grid` / `list`) and (b) is immediately followed by the `loadMoreRef` sentinel (`(isResourcesView || isRecycleView) && (hasMore || isLoadingMore)`). Confirm `contentScrollRef` is the scroll container wrapping it. Do NOT modify the other render block (the Downloads/folders path) or the `justified`/`list` branches.

- [ ] **Step 2: Write the failing test** (`ResourceGrid.virtualization.test.tsx`, vitest + @testing-library/react): mock the necessary context/props to render `ResourceGrid` in `viewMode='grid'`, `isResourcesView=true`, with a large `sortedItems` (e.g. 500 items) inside a scroll container with a mocked height (jsdom: stub `getBoundingClientRect`/`offsetHeight` on the scroll element, or set the virtualizer's scroll/rect via the test). Assert that the number of rendered `ResourceCard` (or a data-testid) DOM nodes is far less than 500 (windowed — e.g. < 60), proving virtualization. (If jsdom measurement is too flaky, instead assert the virtualized container has the `data-virtualized` marker + a spacer element with the computed total height, and that `useGridVirtualizer` is invoked — a structural assertion. Pick the most robust assertion that proves windowing without depending on exact jsdom layout.)

- [ ] **Step 3: Run test, verify it fails.**

- [ ] **Step 4: Implement.** In ONLY the grid-mode branch of the resources keyset path, replace the `<div className="grid ... downloads-grid">{sortedItems.map(item => <CardWrapper/>)}</div>` with a virtualized render:
  - Call `const { columns, rowVirtualizer, containerRef } = useGridVirtualizer({ scrollRef: contentScrollRef, itemCount: sortedItems.length })` (at the top of the component with the other hooks — hooks must not be conditional; compute always, use only in the grid branch).
  - Render: an outer `<div ref={containerRef} style={{ height: rowVirtualizer.getTotalSize(), position:'relative', width:'100%' }} data-virtualized>` containing `rowVirtualizer.getVirtualItems().map(virtualRow => { const start = virtualRow.index * columns; const rowItems = sortedItems.slice(start, start + columns); return <div key={virtualRow.key} ref={rowVirtualizer.measureElement} data-index={virtualRow.index} style={{ position:'absolute', top:0, left:0, width:'100%', transform:\`translateY(${virtualRow.start}px)\`, display:'grid', gridTemplateColumns:\`repeat(${columns}, minmax(0,1fr))\`, gap:'12px' }}>{rowItems.map(item => /* the SAME per-item wrapper + ResourceCard + handlers + badge as the current grid branch */)}</div>; })}`.
  - **Preserve verbatim** the existing per-item JSX (the `<div key={item.id} {...getItemTouchHandlers('file', item)}>` + `<ResourceCard .../>` with all its props: `viewMode="grid"`, badge, onClick, selection, scopeId, onDone, etc.) — only the outer container + the row grouping change.
  - Keep the `loadMoreRef` sentinel exactly where it is (after this block). The IntersectionObserver still fires when the virtual content's bottom (the sized container) scrolls into view.
  - Do NOT change `justified` or `list` branches.

- [ ] **Step 5: Run the test + full suite.** `cd frontend && npm test -- ResourceGrid` + `npm run build` green. Manually reason through: selection, touch, badges, loadMore still wired (cite the preserved lines).

- [ ] **Step 6: Commit:** `feat(frontend): virtualize grid-mode resource library (bounds DOM at 100k)`

---

### Task 3: Build + tests + PR

- [ ] **Step 1:** `cd frontend && npm run build` green + `npm test -- gridColumns ResourceGrid` pass + run the broader `npm test` to confirm no regressions in existing ResourceGrid/Resources tests.
- [ ] **Step 2:** Sanity: confirm `@tanstack/react-virtual` is in `dependencies` (not dev) and the lockfile is committed.
- [ ] **Step 3:** PR:

```bash
git push -u origin feature/resource-grid-virtualization
gh pr create --base master --head feature/resource-grid-virtualization \
  --title "perf(frontend): virtualize grid-mode resource library (100k-ready browsing)" \
  --body "Row-virtualizes the resource library's default **grid** view with @tanstack/react-virtual so the DOM node count stays bounded regardless of library size — a single user can now scroll a 100k+ item library smoothly (previously every keyset-appended item stayed mounted; ~15k items killed the browser). Virtualizes only the grid-mode resources keyset path over the existing scroll container; folders + the infinite-scroll loadMore sentinel + all per-item handlers/badges/selection are preserved verbatim. Column count derived from container width (mobile 2 / desktop auto-fill ~160-220px). list + justified (masonry) modes deferred to a follow-up. No backend change."
```

---

## Self-Review

**Spec coverage:** bound DOM at 100k → Task 2 row virtualization (only visible rows mounted) ✓; default grid mode covered ✓; existing scroll container reused (`contentScrollRef`) ✓; responsive columns (`columnsForWidth`) ✓; variable row height via `measureElement` ✓; loadMore sentinel + per-item handlers/badges preserved ✓; list/justified explicitly deferred ✓; no backend/migration ✓.

**Placeholder scan:** none — the hook signature, the column formula, and the virtualized render structure are concrete.

**Type consistency:** `useGridVirtualizer({ scrollRef, itemCount })` returns `{ columns:number, rowVirtualizer, containerRef }` consumed by Task 2; `columnsForWidth(width:number, isMobile:boolean):number` is the single source of column count used by the hook; `sortedItems` (existing `ResourceItem[]`) is sliced per row. `@tanstack/react-virtual`'s `useVirtualizer`/`Virtualizer` types are used as-imported.
