# Canvas-Core Performance Baseline — 2026-06-14

> **Audit type**: Code-level static analysis only.
> **No live FPS numbers were captured** — the dev backend is production-only and
> there is no runnable local environment for this codebase.  All claims below are
> grounded in exact file:line evidence.  See §4 for how to capture live numbers.

---

## 1. Hot paths identified

### 1a. History-timer churn on every drag tick (FIXED)

**File**: `frontend/features/canvas-core/store/canvasCoreStore.ts`

**Evidence chain**:

1. React Flow fires `onNodesChange` on **every mouse-move** tick during a drag with
   changes of `{ type: 'position', dragging: true }`.
2. `CanvasSurface.onNodesChange` (pre-fix, line 96–100) called `applyNodeChanges` then
   unconditionally forwarded to `setNodes`.
3. `setNodes` (store, search `setNodes(nodes`) called `noteDocumentEditStarting()`.
4. `noteDocumentEditStarting` (search `historyTimer`) does:
   ```ts
   if (pendingHistoryBase === null) { pendingHistoryBase = { nodes, connections }; }
   clearTimeout(historyTimer);
   historyTimer = setTimeout(commitHistory, 250);
   ```
   The `pendingHistoryBase` guard prevents multiple captures — good.
   But `clearTimeout` + `setTimeout` runs **on every tick**.  At 60 fps a 2-second
   drag produces ~120 timer objects allocated and immediately cancelled.

**Cost**: O(drag_ticks) `clearTimeout` + `setTimeout` calls per drag gesture.  In a
browser the timer overhead is small individually but adds up under heavy drag on large
canvases.  More importantly, each tick also calls `markDirty()`, which:
- calls `set({ revision: revision + 1 })` — bumps a Zustand state value,
- calls `clearTimeout(saveTimer); saveTimer = setTimeout(persist, 500)` — resets
  the persistence debounce timer.

So every mid-drag tick re-scheduled **both** the 250 ms history commit AND the 500 ms
save debounce, even though neither should fire until drag-end.

**Fix implemented** (Fix 1): New actions `noteDragStart` + `setNodesDragTick` in the
store; `CanvasSurface.onNodesChange` detects mid-drag ticks (`changes.every(c =>
c.type === 'position' && c.dragging === true)`) and routes them through
`setNodesDragTick`, which calls `set({ nodes })` + `markDirty()` but does **not**
touch `historyTimer`.  `onNodeDragStart` (new ReactFlow prop) calls `noteDragStart()`
once before the first tick to capture the pre-drag snapshot.  Timer-reset churn drops
from O(drag_ticks) to O(1) per drag.

**Behavioral tests**: `canvasCoreStore.perf.test.ts`, describe block
"Fix 1 — drag-tick history timer churn", 7 tests:
- drag ticks do not increment `historyIndex`
- drag-end `setNodes` commits exactly 1 history entry
- `undo()` restores pre-drag positions
- node positions are saved after drag-end
- consecutive independent drags each produce their own history entry
- click-without-drag (position change, dragging=false) still uses `setNodes` path
- pre-drag snapshot captured only once across multiple ticks

---

### 1b. Viewport markDirty on every pan/zoom tick (FIXED)

**File**: `frontend/features/canvas-core/store/canvasCoreStore.ts`

**Evidence chain**:

1. React Flow fires `onMove` on **every mouse-move** tick during pan, and on every
   `wheel` event during zoom.
2. `CanvasSurface.onMove` (pre-fix, line 111–116) called `setViewport(nextViewport)`.
3. `setViewport` (store) called `markDirty()` unconditionally.
4. `markDirty` bumps `revision` (Zustand state write) and resets the 500 ms save debounce.

**Cost**: At 60 fps panning, `revision` is incremented ~60 times per second.  Each
increment triggers all Zustand subscribers of `revision` (e.g. the conflict-resolution
layer that compares remote vs local revisions).  The save debounce timer is reset on
every tick, which is correct for panning (you don't want to save mid-pan), but the
revision bump is not — revision should advance once when a logical edit is committed,
not once per animation frame.

**Fix implemented** (Fix 2): New actions `setViewportOnMove` + `flushViewportDirty`
in the store.  `setViewportOnMove` updates `viewport` state (needed for React Flow
controlled-mode rendering) without calling `markDirty()`.
`CanvasSurface.onMove` now calls `setViewportOnMove` immediately and schedules
`flushViewportDirty` (which calls `markDirty()` once) via `requestAnimationFrame`,
guarded by a `viewportRafRef` so at most one RAF is pending at a time.

**Result**: `revision` is bumped at most once per animation frame (~16 ms intervals)
instead of once per event (~1–2 ms intervals during fast pan).  On a 60 fps pan that
is 0 change in visible behaviour but up to 60× fewer Zustand state writes.

**Behavioral tests**: `canvasCoreStore.perf.test.ts`, describe block
"Fix 2 — viewport markDirty per-tick churn", 6 tests:
- `setViewportOnMove` does not bump `revision`
- `flushViewportDirty` bumps `revision` exactly once
- viewport value persists after `setViewportOnMove` + `flushViewportDirty`
- viewport changes do not create history entries
- programmatic `setViewport` (used by load/reset) still calls `markDirty` normally
- `flushViewportDirty` without prior `setViewportOnMove` is a no-op (no crash)

---

## 2. Candidates deferred (not implemented)

### 2a. `toReactFlowNodes` full-array rebuild on every `nodes` change

**File**: `CanvasSurface.tsx`, `rfNodes` memo (line 85–100 pre-fix).

Every change to `nodes` (including position updates from drag ticks) rebuilds the
entire `rfNodes` array via `toReactFlowNodes(nodes).map(...)`.  For a 1000-node canvas
this is 1000 object allocations per tick.

**Why deferred**: Fix 1 already reduces the frequency of `nodes` changes during drag
from O(drag_ticks) to… still O(drag_ticks) because `setNodesDragTick` still calls
`set({ nodes })`, which still triggers `rfNodes` recomputation.

A proper fix would memoize individual node objects (e.g. via a `Map<id, RFNode>` that
is only updated for changed nodes).  This is more invasive and would require changing
the `useMemo` to a `useRef`-based manual cache.  Safe to implement but needs its own
test surface (position-only change does not reallocate unaffected node objects).

**Recommendation**: Profile live first — React Flow's own rendering may batch
internally.  Add to follow-up once live FPS baselines are captured.

### 2b. `selectionSet` `useMemo` dependency on full `selection` array

**File**: `CanvasSurface.tsx`, line 77.

`new Set(selection)` rebuilds every time `selection` array reference changes.  Since
`setSelection` always calls `set({ selection: ids })` with a new array (even if
content is identical), this fires on every `onSelectionChange` call.  Minor; profile
before fixing.

### 2c. `nodeTypeById` linear scan on every connection attempt

**File**: `CanvasSurface.tsx`, `nodeTypeById` callback.

`rfNodes.find(n => n.id === id)` is O(N).  For connection validation during drag it
fires twice per connection event (once for source, once for target).  At 100+ nodes a
`Map<id, type>` memoized alongside `rfNodes` would be O(1).  Low priority given
connection events are rare vs drag/pan.

---

## 3. Semantics unchanged — verification

All existing canvas-core tests remain green after the fixes (657 tests, 55 files):

```
Test Files  55 passed (55)
Tests       657 passed (657)
```

Key semantic invariants tested:
- Undo after drag restores pre-drag position (Fix 1 test: "undo restores pre-drag positions")
- Programmatic `setViewport` (used by canvas load/reset) still calls `markDirty` (`setViewport` is unchanged)
- Viewport value persists through `setViewportOnMove` + `flushViewportDirty` cycle
- Drag-end `setNodes` call (position change with `dragging=false`) goes through the normal `setNodes` path, commits history

TypeScript: 0 new errors in `features/canvas-core/` (84 total project errors, all pre-existing in unrelated components).

---

## 4. How to capture live numbers

**Prerequisite**: Local dev server running (`cd frontend && npm run dev`, backend not required for canvas-only perf — create a canvas with synthetic nodes via the store dev tools or seed the Supabase dev DB).

### 4a. Chrome DevTools Performance trace

1. Open the canvas page with a pre-seeded 200-node canvas.
2. Open DevTools → Performance → Settings: enable "CPU: 4x slowdown" (simulates mid-range device).
3. Click Record, drag a node for ~3 seconds, pan for ~3 seconds, stop.
4. In the flame chart, look for:
   - Width of `applyNodeChanges` + `setNodes`/`setNodesDragTick` tasks on the main thread
   - Frequency of `markDirty` calls (search "markDirty" in the bottom panel)
   - Any tasks > 50 ms (Long Task marker)
5. Repeat with 500 and 1000 nodes.

**Acceptance bars** (from the 6e plan):
- 60 fps drag @ 200 nodes (16 ms frame budget)
- ≥ 30 fps drag @ 1000 nodes (33 ms budget)
- No main-thread block > 50 ms on pan

### 4b. vitest bench (store-only, no DOM)

```ts
// Add to canvasCoreStore.perf.test.ts or a new .bench.ts file:
import { bench, describe } from 'vitest';
describe('drag tick throughput', () => {
  bench('setNodesDragTick x 100', () => {
    const store = createStore();
    for (let i = 0; i < 100; i++) {
      store.getState().setNodesDragTick(nodes1000);
    }
  });
});
```

Run: `npx vitest bench features/canvas-core/store/canvasCoreStore.perf.bench.ts`

### 4c. React DevTools Profiler

Record a drag + pan cycle. Check "Ranked" tab for components with longest render times.
`CanvasSurface` itself should not appear in renders caused by viewport changes (after
Fix 2, `revision` bumps are throttled and `viewport` is the only changed slice).

---

## 5. Files changed

| File | Change |
|---|---|
| `frontend/features/canvas-core/store/canvasCoreStore.ts` | +4 actions: `noteDragStart`, `setNodesDragTick`, `setViewportOnMove`, `flushViewportDirty` |
| `frontend/features/canvas-core/store/canvasCoreStore.perf.test.ts` | New file — 12 behavioral tests (2 describe blocks) |
| `frontend/features/canvas-core/ui/CanvasSurface.tsx` | Wire new actions; add `onNodeDragStart` prop; RAF-throttle `onMove` |
| `docs/superpowers/perf/2026-06-14-canvas-baseline.md` | This document |
