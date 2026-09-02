/**
 * Autosave arming during an interaction (canvas fluency Wave 2, Task 6).
 *
 * A drag is one edit, not one edit per frame. Every mid-drag tick used to
 * call `markDirty()`, which bumps `revision` and re-arms the 500ms save
 * debounce — so a two-second drag produced ~120 revision bumps and ~120
 * `clearTimeout`/`setTimeout` pairs, and the badge flickered "unsaved" on
 * every frame. None of it bought anything: the save that eventually fires
 * carries the FINAL positions, which the drag-end `setNodes` call marks
 * dirty anyway.
 *
 * What is pinned here:
 *
 *   1. N drag ticks arm NOTHING — no revision bump, no save, not even after
 *      the debounce window has fully elapsed with the drag still held.
 *   2. The drag-end `setNodes` still arms exactly one save, and that save
 *      carries the positions the ticks moved through. The optimisation must
 *      not turn into "drag positions are silently dropped".
 *   3. A pan is the same story on the viewport channel: Task 3 replaced the
 *      per-frame `setViewportOnMove` + RAF `flushViewportDirty` pair with a
 *      single `setViewportSettled` at gesture end, so a pan is one bump.
 *
 * `saveImpl` (the factory's injected save) is the autosave surface these
 * assertions read, matching canvasCoreStore.perf.test.ts — there is no
 * separate `scheduleSave` seam to spy on.
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

const DEBOUNCE_MS = 200;

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

async function loadedStore() {
  const stubs = makeStubs();
  const useStore = createCanvasCoreStore({
    ...stubs,
    debounceMs: DEBOUNCE_MS,
    historyDebounceMs: 9999,
  });
  await useStore.getState().loadCanvas('4242');
  return { stubs, useStore };
}

const at = (x: number) => [{ id: 'a', position: { x, y: 0 } }];

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('autosave arms at the END of a drag, never per tick', () => {
  it('drag ticks never bump revision or arm a save; drag end does exactly once', async () => {
    const { stubs, useStore } = await loadedStore();
    const r0 = useStore.getState().revision;

    useStore.getState().noteDragStart();
    for (let i = 0; i < 10; i++) useStore.getState().setNodesDragTick(at(i * 10));

    expect(useStore.getState().revision).toBe(r0);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(0);

    // Drag end.
    useStore.getState().setNodes(at(100));
    expect(useStore.getState().revision).toBe(r0 + 1);

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
  });

  it('a drag held past the debounce window still saves nothing until it ends', async () => {
    // The strong form: it is not enough that no timer was RE-armed — a tick
    // must not arm one at all, or a long drag saves a mid-gesture position
    // and the badge flickers while the user is still holding the mouse.
    const { stubs, useStore } = await loadedStore();

    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick(at(10));
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS * 5);

    expect(stubs.saveImpl).toHaveBeenCalledTimes(0);
    expect(useStore.getState().revision).toBe(useStore.getState().persistedRevision);
  });

  it('the one save that fires carries the dragged positions, not the pre-drag ones', async () => {
    // The whole point of dropping the per-tick dirty is that it costs
    // nothing: drag-end persistence must still be exact.
    const { stubs, useStore } = await loadedStore();

    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick(at(33));
    useStore.getState().setNodesDragTick(at(66));
    useStore.getState().setNodes([{ id: 'a', position: { x: 99, y: 77 } }]);
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);

    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    const saved = stubs.calls[0].nodes_json as Array<Record<string, unknown>>;
    expect(saved[0].position).toEqual({ x: 99, y: 77 });
  });

  it('an unsaved drag still reaches the server on an explicit flush', async () => {
    // Route leave / surface unmount calls `flushSave()` directly. The ticks
    // did write `nodes` into the store, so the flush must carry them even
    // though no tick ever armed the debounce.
    const { stubs, useStore } = await loadedStore();

    useStore.getState().noteDragStart();
    useStore.getState().setNodesDragTick(at(42));
    await useStore.getState().flushSave();

    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    const saved = stubs.calls[0].nodes_json as Array<Record<string, unknown>>;
    expect(saved[0].position).toEqual({ x: 42, y: 0 });
  });
});

describe('the viewport channel is already once-per-gesture (Task 3)', () => {
  it('has no per-frame viewport writer left to re-arm the debounce', () => {
    const state = useStoreForShape();
    expect(
      (state as unknown as Record<string, unknown>).flushViewportDirty,
    ).toBeUndefined();
    expect(
      (state as unknown as Record<string, unknown>).setViewportOnMove,
    ).toBeUndefined();
  });

  it('a settled pan bumps revision exactly once and arms exactly one save', async () => {
    const { stubs, useStore } = await loadedStore();
    const r0 = useStore.getState().revision;

    useStore.getState().setViewportSettled({ x: 120, y: 40, zoom: 1.25 });
    expect(useStore.getState().revision).toBe(r0 + 1);

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
  });
});

function useStoreForShape() {
  return createCanvasCoreStore(makeStubs()).getState();
}
