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

function withFakeTimers() {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });
}

function makeStubs(initial: Canvas = baseCanvas) {
  let serverRow: Canvas = { ...initial };
  const calls: CanvasUpdatePayload[] = [];

  const loadImpl = vi.fn(async (canvasId: string): Promise<Canvas> => {
    if (canvasId !== serverRow.id) throw new Error('not found');
    return { ...serverRow };
  });

  const saveImpl = vi.fn(
    async (
      canvasId: string,
      payload: CanvasUpdatePayload,
    ): Promise<CanvasSaveResult> => {
      calls.push(payload);
      if (payload.base_updated_at !== serverRow.base_updated_at) {
        return { ok: false, conflict: { ...serverRow } };
      }
      const nextTs = new Date(
        new Date(serverRow.base_updated_at).getTime() + 1000,
      ).toISOString();
      serverRow = {
        ...serverRow,
        ...payload,
        base_updated_at: nextTs,
        updated_at: nextTs,
      } as Canvas;
      return { ok: true, canvas: { ...serverRow } };
    },
  );

  return {
    loadImpl,
    saveImpl,
    calls,
    bumpServer(): void {
      serverRow = {
        ...serverRow,
        base_updated_at: new Date(
          new Date(serverRow.base_updated_at).getTime() + 9999,
        ).toISOString(),
      };
    },
    getServer(): Canvas {
      return { ...serverRow };
    },
  };
}

describe('canvasCoreStore — load', () => {
  it('starts idle', () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    expect(useStore.getState().loadStatus).toBe('idle');
    expect(useStore.getState().canvasId).toBeNull();
  });

  it('populates state from server row on success', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    const s = useStore.getState();
    expect(s.loadStatus).toBe('ready');
    expect(s.canvasId).toBe('4242');
    expect(s.baseUpdatedAt).toBe(baseCanvas.base_updated_at);
    expect(s.viewport).toEqual(baseCanvas.viewport_json);
  });

  it('surfaces load errors', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('does-not-exist');
    const s = useStore.getState();
    expect(s.loadStatus).toBe('error');
    expect(s.loadError).toMatch(/not found/);
  });
});

describe('canvasCoreStore — mutations bump revision', () => {
  it('setViewport / setNodes / setConnections each bump revision', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().revision).toBe(0);

    useStore.getState().setViewport({ x: 10, y: 20, zoom: 1.5 });
    useStore.getState().setNodes([{ id: 'n1' }]);
    useStore.getState().setConnections([{ id: 'e1' }]);

    expect(useStore.getState().revision).toBe(3);
  });

  it('panViewportBy is purely additive', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setViewport({ x: 0, y: 0, zoom: 2 });
    useStore.getState().panViewportBy(5, -3);
    expect(useStore.getState().viewport).toMatchObject({ x: 5, y: -3, zoom: 2 });
  });

  it('zoomViewportAround keeps the world point under the anchor fixed', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().zoomViewportAround({ x: 100, y: 100 }, 2);
    const vp = useStore.getState().viewport;
    // world point that was at (100, 100) before: (100, 100) (identity);
    // after zoom=2 around (100,100), screen of (100,100) world = vp.x + 200
    // → that should still equal 100 → vp.x = -100
    expect(vp.zoom).toBe(2);
    expect(vp.x).toBeCloseTo(-100, 9);
    expect(vp.y).toBeCloseTo(-100, 9);
  });

  it('clamps zoom to MIN/MAX', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().zoomViewportAround({ x: 0, y: 0 }, 1000);
    expect(useStore.getState().viewport.zoom).toBe(8);
  });
});

describe('canvasCoreStore — debounced save', () => {
  withFakeTimers();

  it('does not save immediately', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 500 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'n1' }]);
    expect(stubs.saveImpl).not.toHaveBeenCalled();
  });

  it('saves once after the debounce expires', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 500 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'n1' }]);
    useStore.getState().setNodes([{ id: 'n1' }, { id: 'n2' }]);
    useStore.getState().setNodes([{ id: 'n1' }, { id: 'n2' }, { id: 'n3' }]);

    await vi.advanceTimersByTimeAsync(500);
    // One save with the latest snapshot.
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    expect(stubs.calls[0].nodes_json).toHaveLength(3);
  });

  it('flushSave bypasses the debounce', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 5000 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'n1' }]);
    await useStore.getState().flushSave();
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
  });

  it('bumps baseUpdatedAt after a successful save', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'n1' }]);
    await vi.advanceTimersByTimeAsync(0);
    const s = useStore.getState();
    expect(s.saveStatus).toBe('idle');
    expect(s.baseUpdatedAt).not.toBe(baseCanvas.base_updated_at);
    expect(s.persistedRevision).toBe(1);
  });

  // Task 4 shotSync: a node reconcile flagged `data.stale = true` (its
  // bound shot was deleted) must never round-trip into `nodes_json`. Review
  // fix round 1: an earlier version ALSO cleared it out of local `nodes` in
  // the same tick — that bypassed the store's own undo-history discipline
  // and made an unrelated edit's autosave tick silently delete the stale
  // card's on-screen render, a surprise side effect for an action the user
  // didn't take. Per brief literal ("过滤删除" is about what gets
  // persisted), the payload is filtered but local `nodes` is left alone —
  // the grey stale card stays visible until the canvas reloads.
  it('drops stale-flagged nodes from the save payload but leaves local state alone', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([
      { id: 'n1', data: { stale: true } },
      { id: 'n2', data: { title: 'still here' } },
    ]);
    await vi.advanceTimersByTimeAsync(0);

    expect(stubs.calls[0].nodes_json).toHaveLength(1);
    expect((stubs.calls[0].nodes_json?.[0] as { id: string }).id).toBe('n2');

    // Local state is untouched — the stale node is still there (grey render
    // persists until the next `loadCanvas`), and no phantom mutation went
    // through the undo-history path.
    const s = useStore.getState();
    expect(s.nodes).toHaveLength(2);
    expect(s.nodes.map((n) => (n as { id: string }).id)).toEqual(['n1', 'n2']);
  });

  it('a stale node stays excluded from every subsequent save payload too (re-filtered, not re-added)', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([
      { id: 'n1', data: { stale: true } },
      { id: 'n2', data: { title: 'still here' } },
    ]);
    await vi.advanceTimersByTimeAsync(0);
    expect(stubs.calls[0].nodes_json).toHaveLength(1);

    // A second, unrelated edit ticks another save — the stale node (still
    // sitting in local state) must be filtered again, not silently persist.
    useStore.getState().setNodes([
      { id: 'n1', data: { stale: true } },
      { id: 'n2', data: { title: 'edited' } },
    ]);
    await vi.advanceTimersByTimeAsync(0);
    expect(stubs.calls[1].nodes_json).toHaveLength(1);
    expect((stubs.calls[1].nodes_json?.[0] as { id: string }).id).toBe('n2');
  });

  it('a non-stale save leaves nodes/payload untouched (no spurious filtering)', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'n1', data: { title: 'fine' } }]);
    await vi.advanceTimersByTimeAsync(0);

    expect(stubs.calls[0].nodes_json).toHaveLength(1);
    expect(useStore.getState().nodes).toHaveLength(1);
  });
});

describe('canvasCoreStore — conflict handling', () => {
  withFakeTimers();

  it('surfaces server row on 409 and freezes auto-save', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');

    // Someone else edited the canvas in the meantime.
    stubs.bumpServer();

    useStore.getState().setNodes([{ id: 'loser' }]);
    await vi.advanceTimersByTimeAsync(0);

    const s = useStore.getState();
    expect(s.saveStatus).toBe('error');
    expect(s.saveError).toBe('conflict');
    expect(s.conflict).toBeTruthy();
    expect(s.conflict?.base_updated_at).toBe(stubs.getServer().base_updated_at);

    // Further mutations after conflict do not auto-save until resolved.
    stubs.saveImpl.mockClear();
    useStore.getState().setNodes([{ id: 'still-loser' }]);
    await vi.advanceTimersByTimeAsync(500);
    expect(stubs.saveImpl).not.toHaveBeenCalled();
  });

  it('resolveConflictWithServer adopts server state', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    stubs.bumpServer();
    useStore.getState().setNodes([{ id: 'will-be-lost' }]);
    await vi.advanceTimersByTimeAsync(0);
    const serverAtConflict = useStore.getState().conflict!.base_updated_at;

    useStore.getState().resolveConflictWithServer();
    const s = useStore.getState();
    expect(s.conflict).toBeNull();
    expect(s.baseUpdatedAt).toBe(serverAtConflict);
    expect(s.nodes).toEqual([]); // server row had no nodes
    expect(s.saveStatus).toBe('idle');
    expect(s.revision).toBe(0);
  });

  it('dismissConflict leaves local edits intact and clears conflict', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    stubs.bumpServer();
    useStore.getState().setNodes([{ id: 'local' }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().conflict).toBeTruthy();

    useStore.getState().dismissConflict();
    const s = useStore.getState();
    expect(s.conflict).toBeNull();
    expect(s.saveStatus).toBe('idle');
    expect(s.nodes).toEqual([{ id: 'local' }]);
  });
});

/**
 * `mountEpoch` generation guard (Task 5 评审修复轮1 — "旧卸载 reset 竞态砸新
 * 挂载 → 永久卡 Loading"). `CanvasView`'s unmount cleanup runs
 * `flushSave().finally(() => { if (store.mountEpoch === myEpoch) reset(); })`
 * — these tests exercise that EXACT pattern against the store directly
 * (mirroring how the rest of this file already drives the store without
 * mounting React), since the store is what owns `mountEpoch` and `reset()`.
 *
 * Deliberately does NOT assert on `baseUpdatedAt`/`persistedRevision` after
 * the race resolves: `doSave`'s own post-await `set()` (this file's
 * "debounced save" describe block, function `doSave` in the source) writes
 * unconditionally on `result.ok`, with no epoch check of its own — a STALE
 * save's `persistedRevision`/`baseUpdatedAt` can still land on a NEWER
 * mount's state even with this guard. That is a related but DIFFERENT gap
 * than the one this fix addresses (the reviewer's brief was explicit:
 * "flushSave 本身照常执行" / guard only `reset()`, "选侵入最小") — flagged in
 * the Task 5 report for a future pass, not fixed here.
 */
function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('canvasCoreStore — mount generation guard (Task 5 评审修复轮1)', () => {
  it('loadCanvas() bumps mountEpoch monotonically on every call', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    expect(useStore.getState().mountEpoch).toBe(0);

    await useStore.getState().loadCanvas('4242');
    const first = useStore.getState().mountEpoch;
    expect(first).toBeGreaterThan(0);

    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().mountEpoch).toBeGreaterThan(first);
  });

  it("a stale unmount's flushSave().finally(guarded reset) does NOT clobber a NEWER mount already at ready", async () => {
    const stubs = makeStubs();
    const gate = deferred<void>();
    const hangingSaveImpl = vi.fn(
      async (canvasId: string, payload: CanvasUpdatePayload): Promise<CanvasSaveResult> => {
        await gate.promise;
        return stubs.saveImpl(canvasId, payload);
      },
    );
    const useStore = createCanvasCoreStore({
      loadImpl: stubs.loadImpl,
      saveImpl: hangingSaveImpl,
      debounceMs: 0,
    });

    // OLD instance mounts and gets dirtied (a real edit before the writer
    // switches tabs away).
    await useStore.getState().loadCanvas('4242');
    const oldEpoch = useStore.getState().mountEpoch;
    useStore.getState().setNodes([{ id: 'dirty-from-old-instance' }]);

    // OLD instance "unmounts" — verbatim the same guarded-cleanup pattern
    // `CanvasView`'s effect runs.
    const oldUnmountTail = useStore.getState().flushSave().finally(() => {
      if (useStore.getState().mountEpoch === oldEpoch) {
        useStore.getState().reset();
      }
    });
    expect(hangingSaveImpl).toHaveBeenCalledTimes(1); // save IS in flight, hanging on `gate`

    // NEW instance mounts (writer flipped back to the tab) BEFORE the old
    // flush resolves, and reaches ready.
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().loadStatus).toBe('ready');
    const newEpoch = useStore.getState().mountEpoch;
    expect(newEpoch).not.toBe(oldEpoch);

    // Now let the OLD save resolve — its guarded reset() must see the
    // world has moved on and skip itself.
    gate.resolve();
    await oldUnmountTail;

    // The symptom the reviewer flagged: NOT permanently stuck re-showing
    // "Loading canvas…" — loadStatus/canvasId survive the stale tail.
    expect(useStore.getState().loadStatus).toBe('ready');
    expect(useStore.getState().canvasId).toBe('4242');
  });

  it('a normal unmount with NO newer mount still resets as before (guard is a no-op in the common case)', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });

    await useStore.getState().loadCanvas('4242');
    const myEpoch = useStore.getState().mountEpoch;

    await useStore.getState().flushSave().finally(() => {
      if (useStore.getState().mountEpoch === myEpoch) {
        useStore.getState().reset();
      }
    });

    expect(useStore.getState().loadStatus).toBe('idle');
    expect(useStore.getState().canvasId).toBeNull();
  });

  it('dirty data still gets flushed (saveImpl called) even though the tail-end reset ends up guarded away', async () => {
    const stubs = makeStubs();
    const gate = deferred<void>();
    const hangingSaveImpl = vi.fn(
      async (canvasId: string, payload: CanvasUpdatePayload): Promise<CanvasSaveResult> => {
        await gate.promise;
        return stubs.saveImpl(canvasId, payload);
      },
    );
    const useStore = createCanvasCoreStore({
      loadImpl: stubs.loadImpl,
      saveImpl: hangingSaveImpl,
      debounceMs: 0,
    });

    await useStore.getState().loadCanvas('4242');
    const oldEpoch = useStore.getState().mountEpoch;
    useStore.getState().setNodes([{ id: 'must-not-be-lost' }]);

    const oldUnmountTail = useStore.getState().flushSave().finally(() => {
      if (useStore.getState().mountEpoch === oldEpoch) {
        useStore.getState().reset();
      }
    });
    await useStore.getState().loadCanvas('4242'); // a newer mount takes over
    gate.resolve();
    await oldUnmountTail;

    // The save call itself happened with the dirty payload — the guard only
    // ever short-circuits `reset()`, never `flushSave()`/`doSave()`.
    expect(hangingSaveImpl).toHaveBeenCalledTimes(1);
    expect(hangingSaveImpl.mock.calls[0][1]).toMatchObject({
      nodes_json: [{ id: 'must-not-be-lost' }],
    });
  });
});
