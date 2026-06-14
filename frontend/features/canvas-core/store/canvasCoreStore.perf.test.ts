/**
 * Behavioral tests for Phase 6e performance optimisations.
 *
 * These tests prove correctness WITHOUT live FPS numbers — they verify
 * that the fixes do not change undo/persist semantics while reducing
 * wasteful side effects per tick.
 *
 * Fixes covered:
 *   Fix 1 — Drag-tick history churn
 *     Before: every mid-drag `setNodes` call reset the 250ms history-
 *             debounce timer, producing O(drag_ticks) timer objects.
 *     After : `noteDragStart()` captures the pre-drag snapshot once;
 *             `setNodesDragTick()` updates positions without touching the
 *             history timer; `setNodes()` on drag-end starts the timer
 *             exactly once.
 *
 *   Fix 2 — Viewport markDirty throttle
 *     Before: every `onMove` tick called `markDirty()`, bumping `revision`
 *             and resetting the 500ms save-debounce timer O(pan_ticks)
 *             times per frame.
 *     After : `setViewportOnMove()` updates the viewport without
 *             touching revision; `flushViewportDirty()` (called at RAF
 *             frequency from CanvasSurface) bumps revision ≤1 per frame.
 *
 * IMPORTANT: no live FPS numbers are captured here. See
 * docs/superpowers/perf/2026-06-14-canvas-baseline.md for the methodology
 * to collect them in the real browser.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  Canvas,
  CanvasSaveResult,
  CanvasUpdatePayload,
} from '../types';
import { createCanvasCoreStore } from './canvasCoreStore';

const baseCanvas: Canvas = {
  id: '4242',
  project_id: '111',
  name: 'Untitled',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-06-10T12:00:00+00:00',
  created_at: '2026-06-10T12:00:00+00:00',
  updated_at: '2026-06-10T12:00:00+00:00',
  created_by: null,
};

function makeStubs() {
  const loadImpl = vi.fn(async (): Promise<Canvas> => ({ ...baseCanvas }));
  const calls: CanvasUpdatePayload[] = [];
  const saveImpl = vi.fn(
    async (_id: string, payload: CanvasUpdatePayload): Promise<CanvasSaveResult> => {
      calls.push(payload);
      return { ok: true, canvas: { ...baseCanvas } };
    },
  );
  return { loadImpl, saveImpl, calls };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

// ================================================================
// Fix 1 — Drag history churn
// ================================================================

describe('Fix 1: drag-tick history churn', () => {
  it('N setNodesDragTick calls do NOT start the history-debounce timer', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    // Simulate a drag: capture the pre-drag base, then N position ticks.
    useStore.getState().noteDragStart();
    for (let i = 0; i < 10; i++) {
      useStore.getState().setNodesDragTick([
        { id: 'a', position: { x: i * 10, y: 0 } },
      ]);
    }

    // Advance past historyDebounceMs — if the timer had started it would
    // have committed an entry by now.
    await vi.advanceTimersByTimeAsync(200);

    // History must remain empty: no timer was started by drag ticks.
    expect(useStore.getState().historyPast).toHaveLength(0);
    // canUndo: pendingHistoryBase was captured, so canUndo is true.
    expect(useStore.getState().canUndo()).toBe(true);
  });

  it('setNodes (drag-end) after drag ticks commits exactly 1 entry holding pre-drag state', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');
    // Baseline: no nodes at start.

    // Drag: capture pre-drag snapshot (empty nodes), N ticks, drag-end.
    useStore.getState().noteDragStart();
    for (let i = 0; i < 5; i++) {
      useStore.getState().setNodesDragTick([
        { id: 'a', position: { x: i * 10, y: 0 } },
      ]);
    }
    // Drag end — this is the setNodes call that starts the history timer.
    useStore.getState().setNodes([{ id: 'a', position: { x: 50, y: 0 } }]);

    await vi.advanceTimersByTimeAsync(100); // historyDebounceMs

    // Exactly one history entry capturing the pre-drag state (empty nodes).
    expect(useStore.getState().historyPast).toHaveLength(1);
    expect(useStore.getState().historyPast[0].nodes).toEqual([]);
  });

  it('undo after a drag restores the pre-drag node positions', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    const preDragNodes = [{ id: 'node-A', position: { x: 0, y: 0 } }];
    useStore.getState().setNodes(preDragNodes);
    await vi.advanceTimersByTimeAsync(100); // commit initial state to history

    // Drag node-A from (0,0) through (100,100), (200,200) to (300,400).
    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick([
      { id: 'node-A', position: { x: 100, y: 100 } },
    ]);
    useStore.getState().setNodesDragTick([
      { id: 'node-A', position: { x: 200, y: 200 } },
    ]);
    // Drag end
    useStore.getState().setNodes([
      { id: 'node-A', position: { x: 300, y: 400 } },
    ]);
    await vi.advanceTimersByTimeAsync(100);

    // Current position is the drag-end position.
    const obj = useStore.getState().nodes[0] as Record<string, unknown>;
    expect(obj.position).toEqual({ x: 300, y: 400 });

    // Undo → pre-drag position.
    useStore.getState().undo();
    const afterUndo = useStore.getState().nodes[0] as Record<string, unknown>;
    expect(afterUndo.position).toEqual({ x: 0, y: 0 });
  });

  it('setNodesDragTick marks dirty so positions ARE saved after debounce', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 200,
      historyDebounceMs: 9999,
    });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick([
      { id: 'a', position: { x: 99, y: 77 } },
    ]);

    // Save hasn't fired yet (debounce not expired).
    expect(stubs.saveImpl).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(200);

    // One save with the latest node positions.
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    const saved = stubs.calls[0].nodes_json as Array<Record<string, unknown>>;
    expect(saved[0].position).toEqual({ x: 99, y: 77 });
  });

  it('consecutive drags without a pause share one history entry', async () => {
    // Two back-to-back drags within the same debounce window should produce
    // exactly ONE history entry (the state before the FIRST drag).
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    // First drag
    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick([{ id: 'a', position: { x: 10, y: 0 } }]);
    useStore.getState().setNodes([{ id: 'a', position: { x: 20, y: 0 } }]);

    // Second drag starts immediately (timer hasn't fired yet — pendingHistoryBase
    // is still set, so the guard keeps the original pre-first-drag base).
    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick([{ id: 'a', position: { x: 30, y: 0 } }]);
    useStore.getState().setNodes([{ id: 'a', position: { x: 40, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(100);

    // One entry: state before the first drag (empty nodes).
    expect(useStore.getState().historyPast).toHaveLength(1);
    expect(useStore.getState().historyPast[0].nodes).toEqual([]);
  });

  it('noteDragStart when no drag follows does not block subsequent setNodes history', async () => {
    // If onNodeDragStart fires but the user releases without moving (click),
    // the next setNodes call should still capture history correctly.
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().noteDragStart(); // captures base = []
    // No drag ticks — user just clicked, no movement.

    // A selection-change setNodes call (not a drag) starts the history timer.
    useStore.getState().setNodes([{ id: 'a', selected: true }]);
    await vi.advanceTimersByTimeAsync(100);

    // One history entry: pre-click state (empty nodes).
    expect(useStore.getState().historyPast).toHaveLength(1);
    expect(useStore.getState().historyPast[0].nodes).toEqual([]);
  });
});

// ================================================================
// Fix 2 — Viewport markDirty throttle
// ================================================================

describe('Fix 2: viewport markDirty throttle', () => {
  it('N setViewportOnMove calls update viewport but do NOT bump revision', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');

    const initialRevision = useStore.getState().revision;

    for (let i = 0; i < 10; i++) {
      useStore.getState().setViewportOnMove({ x: i * 5, y: 0, zoom: 1 });
    }

    // Viewport IS updated to the latest value.
    expect(useStore.getState().viewport.x).toBe(45);
    // Revision NOT bumped — no markDirty was called.
    expect(useStore.getState().revision).toBe(initialRevision);
  });

  it('flushViewportDirty bumps revision exactly once regardless of prior tick count', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');

    const initialRevision = useStore.getState().revision;

    // 10 viewport ticks + 1 RAF flush.
    for (let i = 0; i < 10; i++) {
      useStore.getState().setViewportOnMove({ x: i * 5, y: 0, zoom: 1 });
    }
    useStore.getState().flushViewportDirty();

    expect(useStore.getState().revision).toBe(initialRevision + 1);
  });

  it('viewport persists after flushViewportDirty + debounce', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().setViewportOnMove({ x: 111, y: 222, zoom: 1.5 });
    useStore.getState().flushViewportDirty();

    await vi.advanceTimersByTimeAsync(100);

    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    expect(stubs.calls[0].viewport_json).toMatchObject({ x: 111, y: 222 });
  });

  it('viewport changes via setViewportOnMove do NOT create history entries', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    for (let i = 0; i < 20; i++) {
      useStore.getState().setViewportOnMove({ x: i * 10, y: 0, zoom: 1 });
    }
    useStore.getState().flushViewportDirty();
    await vi.advanceTimersByTimeAsync(200);

    expect(useStore.getState().historyPast).toHaveLength(0);
    expect(useStore.getState().canUndo()).toBe(false);
  });

  it('programmatic setViewport (pan/zoom actions) still bumps revision immediately', async () => {
    // Verify the existing controlled-viewport path (panViewportBy, zoomViewportAround,
    // undo/redo) is not affected by Fix 2.
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');

    const revBefore = useStore.getState().revision;
    useStore.getState().setViewport({ x: 5, y: 10, zoom: 2 });
    expect(useStore.getState().revision).toBe(revBefore + 1);
  });

  it('flushViewportDirty without prior setViewportOnMove still schedules a save of existing data', async () => {
    // Edge case: calling flushViewportDirty with no preceding setViewportOnMove.
    // It should still mark dirty and trigger a save.
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    await useStore.getState().loadCanvas('4242');

    // Set some data first so there's something to save.
    useStore.getState().setNodes([{ id: 'x' }]);
    await vi.advanceTimersByTimeAsync(100); // first save
    stubs.saveImpl.mockClear();
    stubs.calls.length = 0;

    // Now call flushViewportDirty without a setViewportOnMove.
    useStore.getState().flushViewportDirty();
    await vi.advanceTimersByTimeAsync(100);

    // A save was triggered.
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
  });
});
