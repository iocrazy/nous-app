/**
 * Tests for the undo/redo + selection slice of the canvas store
 * (Phase 1 Week 3).
 *
 * History captures only document state (nodes + connections), NOT
 * viewport or selection, by design.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Canvas, CanvasSaveResult, CanvasUpdatePayload } from '../types';
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
  const saveImpl = vi.fn(
    async (_id: string, _p: CanvasUpdatePayload): Promise<CanvasSaveResult> => ({
      ok: true,
      canvas: { ...baseCanvas },
    }),
  );
  return { loadImpl, saveImpl };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// ============================================================
// Selection
// ============================================================

describe('selection', () => {
  it('starts empty after load', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().selection).toEqual([]);
  });

  it('setSelection dedupes ids', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setSelection(['n1', 'n2', 'n1', 'n3']);
    expect(useStore.getState().selection).toEqual(['n1', 'n2', 'n3']);
  });

  it('selectAll picks up every node with a string id', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore
      .getState()
      .setNodes([{ id: 'a' }, { id: 'b' }, { type: 'no-id' }, { id: 'c' }]);
    useStore.getState().selectAll();
    expect(useStore.getState().selection).toEqual(['a', 'b', 'c']);
  });

  it('clearSelection resets to []', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setSelection(['n1']);
    useStore.getState().clearSelection();
    expect(useStore.getState().selection).toEqual([]);
  });

  it('mutations do not touch selection', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setSelection(['n1', 'n2']);
    useStore.getState().setNodes([{ id: 'foo' }]);
    expect(useStore.getState().selection).toEqual(['n1', 'n2']);
  });

  it('load resets selection', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setSelection(['stale']);
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().selection).toEqual([]);
  });
});

// ============================================================
// History — capture + cap
// ============================================================

describe('history capture', () => {
  it('canUndo / canRedo start false', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().canUndo()).toBe(false);
    expect(useStore.getState().canRedo()).toBe(false);
  });

  it('a setNodes edit makes canUndo true before the debounce fires', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    // Pending base exists → canUndo is true even pre-debounce.
    expect(useStore.getState().canUndo()).toBe(true);
  });

  it('viewport edits do NOT create history entries', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setViewport({ x: 10, y: 10, zoom: 2 });
    useStore.getState().panViewportBy(5, 5);
    useStore.getState().zoomViewportAround({ x: 0, y: 0 }, 1.5);
    await vi.advanceTimersByTimeAsync(500);
    expect(useStore.getState().historyPast).toEqual([]);
    expect(useStore.getState().canUndo()).toBe(false);
  });

  it('rapid edits inside the debounce window collapse to ONE history entry', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');
    // No nodes initially → first burst has base = [].
    useStore.getState().setNodes([{ id: 'a' }]);
    useStore.getState().setNodes([{ id: 'a' }, { id: 'b' }]);
    useStore.getState().setNodes([{ id: 'a' }, { id: 'b' }, { id: 'c' }]);
    await vi.advanceTimersByTimeAsync(100);
    expect(useStore.getState().historyPast).toHaveLength(1);
    // The entry holds the PRE-edit state — empty nodes.
    expect(useStore.getState().historyPast[0].nodes).toEqual([]);
  });

  it('caps history at 30 entries (oldest dropped)', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 50,
    });
    await useStore.getState().loadCanvas('4242');
    for (let i = 0; i < 35; i++) {
      useStore.getState().setNodes([{ id: `n${i}` }]);
      await vi.advanceTimersByTimeAsync(50);
    }
    const past = useStore.getState().historyPast;
    expect(past).toHaveLength(30);
    // Oldest 5 entries were dropped — entry 0 should hold the state from
    // the start of burst #5, which is [{id:'n4'}].
    expect(past[0].nodes).toEqual([{ id: 'n4' }]);
  });
});

// ============================================================
// History — undo / redo round-trip
// ============================================================

describe('undo / redo', () => {
  it('undo restores the pre-edit nodes and exposes redo', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 50,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    await vi.advanceTimersByTimeAsync(50);
    useStore.getState().setNodes([{ id: 'a' }, { id: 'b' }]);
    await vi.advanceTimersByTimeAsync(50);

    expect(useStore.getState().nodes).toEqual([{ id: 'a' }, { id: 'b' }]);

    useStore.getState().undo();
    expect(useStore.getState().nodes).toEqual([{ id: 'a' }]);
    expect(useStore.getState().canRedo()).toBe(true);

    useStore.getState().undo();
    expect(useStore.getState().nodes).toEqual([]);
    expect(useStore.getState().canUndo()).toBe(false);
  });

  it('redo reapplies a popped state', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 50,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    await vi.advanceTimersByTimeAsync(50);

    useStore.getState().undo();
    expect(useStore.getState().nodes).toEqual([]);
    useStore.getState().redo();
    expect(useStore.getState().nodes).toEqual([{ id: 'a' }]);
  });

  it('a new edit invalidates the redo stack', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 50,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    await vi.advanceTimersByTimeAsync(50);
    useStore.getState().undo(); // redo stack now has [a]
    expect(useStore.getState().canRedo()).toBe(true);

    useStore.getState().setNodes([{ id: 'forked' }]);
    await vi.advanceTimersByTimeAsync(50);
    expect(useStore.getState().canRedo()).toBe(false);
  });

  it('undo while an edit burst is pending flushes that burst first', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 999,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    // history debounce has NOT fired yet
    expect(useStore.getState().historyPast).toHaveLength(0);

    useStore.getState().undo();
    // The pending base ([]) was committed THEN popped — net effect:
    // back to the pre-edit state.
    expect(useStore.getState().nodes).toEqual([]);
  });

  it('undo / redo bump revision so the change persists', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 50,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    await vi.advanceTimersByTimeAsync(50);
    const after = useStore.getState().revision;
    useStore.getState().undo();
    expect(useStore.getState().revision).toBe(after + 1);
    useStore.getState().redo();
    expect(useStore.getState().revision).toBe(after + 2);
  });

  it('reset clears history and selection', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 50,
    });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'a' }]);
    useStore.getState().setSelection(['a']);
    await vi.advanceTimersByTimeAsync(50);

    useStore.getState().reset();
    const s = useStore.getState();
    expect(s.historyPast).toEqual([]);
    expect(s.historyFuture).toEqual([]);
    expect(s.selection).toEqual([]);
    expect(s.nodes).toEqual([]);
  });
});
