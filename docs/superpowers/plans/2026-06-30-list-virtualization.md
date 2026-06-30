# List-Mode Resource Grid Virtualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the #927 grid virtualization to the **list** view mode of the resource library, so browsing a 100k+ item library in list view also stays bounded (DOM-wise) and smooth.

**Architecture:** #927 virtualized only the `grid` branch of the resources keyset path (`ResourceGrid.tsx`, the `data-virtualized="grid"` block) using `useGridVirtualizer` (responsive columns). The `list` branch (`space-y-1.5` → `sortedItems.map`) and `justified` (masonry) branch still render every accumulated item. This plan virtualizes the **list** branch by parametrizing `useGridVirtualizer` with a fixed single column for list mode and mirroring the grid block's row-virtualized render at 1 item/row, full width. `justified` (variable-width masonry) remains deferred — it needs a different layout-aware virtualizer.

**Tech Stack:** React 19 + Vite + Tailwind + `@tanstack/react-virtual` (already a dep). Frontend: `cd frontend && npm run build` + `npm test` (vitest).

## Global Constraints

- **Only the `list` branch of the resources keyset path changes.** Do NOT touch the already-virtualized `grid` branch (`data-virtualized="grid"`), the `justified` branch, the folder section, the Downloads/folders render block, or the `loadMoreRef` sentinel.
- **Reuse the existing scroll container** `contentScrollRef` and the existing single `useGridVirtualizer` call (do NOT add a second virtualizer instance / second scroll container).
- **List = one item per row, full width.** Column count for list mode is **1** (not the responsive 2–8 used for grid). Add a `fixedColumns?: number` option to `useGridVirtualizer`: when provided, it bypasses the width-derived column calc and uses that value; when absent, behavior is unchanged (responsive).
- **Variable row height via `measureElement`** with a list-appropriate `estimateRowHeight` (~64px; grid stays ~280px). Pick the estimate from the active `viewMode`.
- **Preserve list-row behavior verbatim** — the existing per-item JSX in the list branch (the `<div key={item.id} {...getItemTouchHandlers('file', item)}>` wrapper + `<ResourceCard viewMode="list" .../>` with ALL its props + badge) is copied verbatim into the virtualized rows. Only the container + row grouping change.
- **Hooks stay unconditional.** The single `useGridVirtualizer` call stays at the top; its `fixedColumns`/`estimateRowHeight` are derived from `viewMode` (a prop/state already available there). Switching viewMode just re-renders with new params.
- **No backend change, no migration.**

---

### Task 1: Add `fixedColumns` option to useGridVirtualizer

**Files:**
- Modify: `frontend/hooks/useGridVirtualizer.ts`
- Modify: `frontend/utils/gridColumns.test.ts` or a new `frontend/hooks/useGridVirtualizer` note (the pure helper is unchanged; test the option via the hook is optional — at minimum keep build green)

**Interfaces:**
- `useGridVirtualizer({ scrollRef, itemCount, estimateRowHeight?, fixedColumns? }: { scrollRef, itemCount, estimateRowHeight?: number, fixedColumns?: number })`: when `fixedColumns` is a positive number, `columns = fixedColumns` and the width-derived calc is skipped (the ResizeObserver may stay for layout but does not drive columns); otherwise unchanged (responsive `columnsForWidth(width, ...)`). `rowCount = Math.ceil(itemCount / columns)` with `columns>=1` guard.

- [ ] **Step 1: Read** `frontend/hooks/useGridVirtualizer.ts` (the current responsive-columns logic + ResizeObserver + the `useVirtualizer` setup).

- [ ] **Step 2: Implement** the `fixedColumns?` option: in the columns derivation, `const columns = (fixedColumns && fixedColumns > 0) ? fixedColumns : columnsForWidth(width, width > 0 && width < 768)`. Keep everything else (ResizeObserver, rowCount, virtualizer, return shape) unchanged. The `estimateRowHeight` already flows into `estimateSize`.

- [ ] **Step 3: Build + test.** `cd frontend && npm run build` green + `npm test -- gridColumns` stays green. (Optionally add a small hook test asserting `fixedColumns:1` yields `columns===1` regardless of width — only if a hook-test harness exists; otherwise the integration test in Task 2 covers it.) **Commit:** `feat(frontend): fixedColumns option for useGridVirtualizer (list mode)`

---

### Task 2: Virtualize the list branch in ResourceGrid

**Files:**
- Modify: `frontend/components/ResourceGrid.tsx` (the `list` branch of the resources keyset path + the `useGridVirtualizer` call params)
- Test: `frontend/components/ResourceGrid.listVirtualization.test.tsx` (new; mirror `ResourceGrid.virtualization.test.tsx`)

**Interfaces:**
- Consumes: `useGridVirtualizer` with `fixedColumns` (Task 1).

- [ ] **Step 1: Read** `ResourceGrid.tsx`. Locate: the single `useGridVirtualizer({ scrollRef: contentScrollRef, itemCount: sortedItems.length })` call (~line 400); the already-virtualized `grid` branch (`data-virtualized="grid"`, ~1169); the **list** branch (`viewMode` else → `<div className="space-y-1.5">{sortedItems.map(...)}</div>`, ~1254) in the SAME keyset block that ends with the `loadMoreRef` sentinel (~1341). Confirm `ResourceCard` is rendered with `viewMode="list"` in that branch.

- [ ] **Step 2: Write the failing test** (`ResourceGrid.listVirtualization.test.tsx`, mirror the grid one): render `ResourceGrid` with `viewMode='list'`, `isResourcesView=true`, a large `sortedItems` (~500), mock `useGridVirtualizer` to return e.g. 5 single-column rows; assert a `data-virtualized="list"` container is present AND far fewer than 500 `ResourceCard`/per-card nodes render (windowed). Use the same robust structural assertion approach as the grid test.

- [ ] **Step 3: Run test, verify fail.**

- [ ] **Step 4: Implement.**
  - Change the `useGridVirtualizer` call (~line 400) to derive params from `viewMode`:
    ```tsx
    const { columns, rowVirtualizer, containerRef: virtualContainerRef } = useGridVirtualizer({
      scrollRef: contentScrollRef,
      itemCount: sortedItems.length,
      fixedColumns: viewMode === 'list' ? 1 : undefined,
      estimateRowHeight: viewMode === 'list' ? 64 : 280,
    });
    ```
  - Replace ONLY the list branch's `<div className="space-y-1.5">{sortedItems.map(item => <listRow/>)}</div>` with a virtualized render mirroring the grid block:
    ```tsx
    <div ref={virtualContainerRef} style={{ height: rowVirtualizer.getTotalSize(), position: 'relative', width: '100%' }} data-virtualized="list">
      {rowVirtualizer.getVirtualItems().map((virtualRow) => {
        const item = sortedItems[virtualRow.index];   // columns===1 for list → 1 item per row
        if (!item) return null;
        return (
          <div key={virtualRow.key} data-index={virtualRow.index} ref={rowVirtualizer.measureElement}
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${virtualRow.start}px)`, paddingBottom: '6px' }}>
            {/* THE EXACT per-item JSX from the current list branch — the <div key={item.id} {...getItemTouchHandlers('file', item)}> wrapper + <ResourceCard viewMode="list" ... /> with ALL props + badge, copied verbatim */}
          </div>
        );
      })}
    </div>
    ```
    (`paddingBottom: '6px'` reproduces the `space-y-1.5` = 6px vertical gap, included in `measureElement` so row positions are correct.) `columns` is 1 for list, so each virtual row maps to exactly `sortedItems[virtualRow.index]`.
  - **Preserve the per-item JSX verbatim** (badge computation moved inside the row; ResourceCard props unchanged, `viewMode="list"`).
  - Do NOT change the `grid`/`justified` branches or the sentinel.

- [ ] **Step 5: Run the test + suite.** `cd frontend && npm test -- ResourceGrid` (both grid + list virtualization tests pass) + `npm run build` green. Verify the grid branch still works (its test unchanged).

- [ ] **Step 6: Commit:** `feat(frontend): virtualize list-mode resource library (bounds DOM at 100k)`

---

### Task 3: Build + tests + PR

- [ ] **Step 1:** `cd frontend && npm run build` green + `npm test -- ResourceGrid gridColumns` pass + a broad `npm test -- Resource` for regressions.
- [ ] **Step 2:** PR:

```bash
git push -u origin feature/list-virtualization
gh pr create --base master --head feature/list-virtualization \
  --title "perf(frontend): virtualize list-mode resource library (100k-ready browsing)" \
  --body "Extends the #927 grid virtualization to the **list** view mode: the resources keyset path's list branch is now row-virtualized (1 item/row, full width) over the existing scroll container, so browsing a 100k+ library in list view stays DOM-bounded. Adds a fixedColumns option to useGridVirtualizer (list = 1 column; grid stays responsive). The grid branch, justified (masonry) branch, folders, the loadMore sentinel, and all per-item handlers/badges are preserved. justified/masonry virtualization (variable-width layout) remains a separate follow-up. No backend change."
```

---

## Self-Review

**Spec coverage:** list-mode virtualization → Task 2 (fixedColumns=1, 1 item/row, windowed) ✓; reuse existing virtualizer + scroll container (single hook call parametrized by viewMode) ✓; per-item list JSX + badge + handlers preserved ✓; grid/justified/folders/sentinel untouched ✓; 6px row gap preserved via paddingBottom ✓; justified deferred ✓; no backend/migration ✓.

**Placeholder scan:** none — the hook option, the call params, and the virtualized list render are concrete.

**Type consistency:** `useGridVirtualizer` gains optional `fixedColumns?: number` / `estimateRowHeight?: number`; the call passes `viewMode === 'list' ? 1 : undefined`. `columns===1` ⇒ `sortedItems[virtualRow.index]` is the single row item. `rowVirtualizer`/`measureElement` types are unchanged from #927.
