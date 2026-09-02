/**
 * The store's viewport contract after canvas fluency Wave 1, Task 3.
 *
 * The old design had TWO viewport writers for one gesture: `setViewportOnMove`
 * wrote the transform on every animation frame (so React Flow, which was
 * running CONTROLLED, could render it), and a RAF-scheduled
 * `flushViewportDirty` bumped `revision` at most once per frame. Both existed
 * only to make a per-frame React state write survivable.
 *
 * React Flow now owns the transform (uncontrolled, seeded once via
 * `defaultViewport`), so no pan frame reaches the store at all. What is left
 * is one function for one event: the gesture settled, here is where it landed,
 * persist it. That is `setViewportSettled`.
 *
 * `scheduleSave` is observed through its only externally visible effect — a
 * call to the injected `saveImpl` after the debounce elapses. Spying on the
 * private timer would pass even if the timer never reached the network.
 *
 * Fix round 1 adds `viewportEpoch`: the store cannot reach React Flow's
 * instance, so a viewport it writes ITSELF (load, switch, realtime rebase,
 * conflict resolve) has to announce that the canvas needs moving. A viewport
 * that arrived FROM the canvas (`setViewportSettled`) must not — it is already
 * on screen, and re-applying it would fight the user mid-pan.
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
  const calls: CanvasUpdatePayload[] = [];
  const saveImpl = vi.fn(
    async (_id: string, payload: CanvasUpdatePayload): Promise<CanvasSaveResult> => {
      calls.push(payload);
      return {
        ok: true,
        canvas: { ...baseCanvas, base_updated_at: new Date().toISOString() },
      };
    },
  );
  return { loadImpl, saveImpl, calls };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('canvasCoreStore — settled viewport', () => {
  it('a settled viewport bumps revision exactly once and schedules exactly one save', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    await useStore.getState().loadCanvas('4242');

    const before = useStore.getState().revision;
    useStore.getState().setViewportSettled({ x: 1, y: 2, zoom: 1.5 });

    expect(useStore.getState().revision).toBe(before + 1);
    expect(useStore.getState().viewport).toEqual({ x: 1, y: 2, zoom: 1.5 });

    await vi.advanceTimersByTimeAsync(100);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    expect(stubs.calls[0].viewport_json).toMatchObject({ x: 1, y: 2, zoom: 1.5 });
  });

  it('has no per-frame viewport writer any more', () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    const state = useStore.getState() as unknown as Record<string, unknown>;

    expect(state.setViewportOnMove).toBeUndefined();
    expect(state.flushViewportDirty).toBeUndefined();
  });

  it('clamps an out-of-range zoom on the way in', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().setViewportSettled({ x: 0, y: 0, zoom: 9000 });
    expect(useStore.getState().viewport.zoom).toBe(8);
  });

  it('does not create an undo entry — panning is not a document edit', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().setViewportSettled({ x: 111, y: 222, zoom: 1.5 });
    await vi.advanceTimersByTimeAsync(200);

    expect(useStore.getState().historyPast).toHaveLength(0);
    expect(useStore.getState().canUndo()).toBe(false);
  });
});

describe('canvasCoreStore — viewportEpoch (store-side writes announce themselves)', () => {
  it('a load bumps the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    const before = useStore.getState().viewportEpoch;

    await useStore.getState().loadCanvas('4242');

    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });

  it('a realtime rebase bumps the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().viewportEpoch;

    useStore.getState().applyRemoteUpdate({
      ...baseCanvas,
      viewport_json: { x: 7, y: 8, zoom: 2 },
      base_updated_at: '2026-06-10T13:00:00+00:00',
    });

    expect(useStore.getState().viewport).toEqual({ x: 7, y: 8, zoom: 2 });
    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });

  it('a conflict resolve bumps the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.setState({
      conflict: {
        ...baseCanvas,
        viewport_json: { x: -1, y: -2, zoom: 3 },
        base_updated_at: '2026-06-10T14:00:00+00:00',
      },
    });
    const before = useStore.getState().viewportEpoch;

    useStore.getState().resolveConflictWithServer();

    expect(useStore.getState().viewport).toEqual({ x: -1, y: -2, zoom: 3 });
    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });

  it('a settled viewport does NOT bump the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().viewportEpoch;

    useStore.getState().setViewportSettled({ x: 1, y: 2, zoom: 1.5 });
    useStore.getState().setViewportSettled({ x: 3, y: 4, zoom: 1.6 });

    expect(useStore.getState().viewportEpoch).toBe(before);
  });

  it('the legacy programmatic writers bump it too — they are store-side writes', async () => {
    // `setViewport` / `panViewportBy` / `zoomViewportAround` have no
    // production callers, but they are the shape a future caller would reach
    // for, and under an uncontrolled surface a store write that does not
    // announce itself simply does not move the canvas.
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().viewportEpoch;

    useStore.getState().setViewport({ x: 1, y: 1, zoom: 1 });
    useStore.getState().panViewportBy(5, 5);
    useStore.getState().zoomViewportAround({ x: 0, y: 0 }, 1.5);

    expect(useStore.getState().viewportEpoch).toBe(before + 3);
  });
});
