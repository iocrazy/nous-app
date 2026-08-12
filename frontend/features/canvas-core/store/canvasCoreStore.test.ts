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

  // Final review Important 3 — the reverse transition (shotSync's
  // `reconcileShotNodes` now patches `stale: false` back onto a node whose
  // shot reappeared, e.g. an Undo after an agent deletion). `isStaleNode`
  // is a pure `data.stale === true` check, so once that patch lands the
  // node simply falls out of the filter on its own — this pins the
  // end-to-end contract (patchNode → save payload), not shotSync's own
  // diff logic (that's `shotSync.test.ts`'s job).
  it('a revived node (stale patched back to false via patchNode) re-enters the save payload', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([
      { id: 'n1', data: { stale: true } },
      { id: 'n2', data: { title: 'still here' } },
    ]);
    await vi.advanceTimersByTimeAsync(0);
    expect(stubs.calls[0].nodes_json).toHaveLength(1);

    // The shot came back — reconcile's own mutation API is `patchNode`,
    // same as every other mirror-field write.
    useStore.getState().patchNode('n1', { data: { stale: false } });
    await vi.advanceTimersByTimeAsync(0);

    expect(stubs.calls[1].nodes_json).toHaveLength(2);
    expect(stubs.calls[1].nodes_json?.map((n) => (n as { id: string }).id).sort()).toEqual([
      'n1',
      'n2',
    ]);
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

/**
 * `doSave`'s own post-await `set()` calls (Task 5 评审修复轮2 — escalated
 * from a "flagged but deferred" note in 修复轮1 to a real data-loss path on
 * re-review). `revision`/`persistedRevision` both reset to 0 on every
 * `loadCanvas()` — small integers collide across mounts constantly (the
 * very first edit after ANY mount lands on revision 1). If an OLD mount's
 * `doSave` resolves AFTER a NEWER mount has loaded and made its own edit
 * that happens to land on the SAME revision number, the old save's
 * unconditional `persistedRevision: snapshot.revision` write marks the
 * NEW mount's real, unsaved edit as "already saved" — the next
 * `doSave`/`flushSave` hits the `persistedRevision >= revision` early
 * return and the edit is silently never sent. The 409 branch has the same
 * shape of bug: a stale conflict can paint a false conflict banner over a
 * newer, clean mount.
 */
describe('canvasCoreStore — doSave post-write mount generation guard (Task 5 评审修复轮2)', () => {
  it('a stale doSave success resolving after a NEWER mount made a SAME-revision edit does not mark that edit saved (data-loss regression)', async () => {
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

    // OLD mount: load, edit (revision 0→1), flushSave kicks off and hangs.
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'old-edit' }]);
    expect(useStore.getState().revision).toBe(1);
    const oldTail = useStore.getState().flushSave();
    expect(hangingSaveImpl).toHaveBeenCalledTimes(1);

    // NEW mount takes over — `loadCanvas` resets revision/persistedRevision
    // to 0 — and makes its OWN edit, which lands on revision 1 too (the
    // collision this bug depends on; both mounts' FIRST edit always does).
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'new-unsaved-edit' }]);
    expect(useStore.getState().revision).toBe(1); // collision confirmed

    // Let the stale OLD save resolve.
    gate.resolve();
    await oldTail;

    // The bug: `persistedRevision` getting set to 1 here would make the
    // store believe the NEW mount's edit is already saved.
    const s = useStore.getState();
    expect(s.persistedRevision).toBe(0); // NOT marked saved
    expect(s.nodes).toEqual([{ id: 'new-unsaved-edit' }]); // local edit intact

    // Direct proof it isn't lost: a subsequent flushSave actually sends it.
    hangingSaveImpl.mockClear();
    await useStore.getState().flushSave();
    expect(hangingSaveImpl).toHaveBeenCalledTimes(1);
    expect(hangingSaveImpl.mock.calls[0][1]).toMatchObject({
      nodes_json: [{ id: 'new-unsaved-edit' }],
    });
  });

  it('a stale doSave 409 tail does not paint a conflict banner onto a NEWER, already-clean mount', async () => {
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
    useStore.getState().setNodes([{ id: 'old-edit' }]);
    const oldTail = useStore.getState().flushSave(); // in flight, hanging

    // Server moves on (another session/tab saves) while the OLD save is
    // hanging — its stale base_updated_at will 409 once it finally lands.
    stubs.bumpServer();

    // NEW mount loads cleanly.
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().saveStatus).toBe('idle');
    expect(useStore.getState().conflict).toBeNull();

    gate.resolve();
    await oldTail;

    // The stale 409 must not have painted anything onto the NEW mount.
    const s = useStore.getState();
    expect(s.conflict).toBeNull();
    expect(s.saveStatus).toBe('idle');
  });

  it('a normal (single-mount, no race) save still applies its post-set as before', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');
    useStore.getState().setNodes([{ id: 'n1' }]);
    await useStore.getState().flushSave();

    const s = useStore.getState();
    expect(s.persistedRevision).toBe(1);
    expect(s.saveStatus).toBe('idle');
    expect(s.baseUpdatedAt).not.toBe(baseCanvas.base_updated_at);
  });
});

// 2026-08-12 production incident — duplicated node ids blank the whole
// React Flow surface (nodes stay permanently `visibility:hidden`). Three
// defense layers, each pinned here: load-time sanitize (applyServerRow),
// the write-point guard (appendElementsNoHistory), and the reconcile
// self-heal channel's applier (dedupeNodes).
describe('canvasCoreStore — duplicate-node-id defenses (2026-08-12)', () => {
  it('loadCanvas collapses duplicated ids in nodes_json down to the first occurrence', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const stubs = makeStubs({
      ...baseCanvas,
      nodes_json: [
        { id: 'shot-1', type: 'shot', position: { x: 1, y: 1 }, data: { shot_id: '1' } },
        { id: 'shot-1', type: 'shot', position: { x: 2, y: 2 }, data: { shot_id: '1' } },
        { id: 'shot-1', type: 'shot', position: { x: 3, y: 3 }, data: { shot_id: '1' } },
        { id: 'shot-2', type: 'shot', position: { x: 4, y: 4 }, data: { shot_id: '2' } },
      ],
    });
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('4242');

    const nodes = useStore.getState().nodes as Array<{ id: string; position: { x: number } }>;
    expect(nodes.map((n) => n.id)).toEqual(['shot-1', 'shot-2']);
    // First occurrence survives (it carries the user-arranged position).
    expect(nodes[0].position.x).toBe(1);
    // A pure load is NOT dirty — sanitizing must not autosave on open.
    expect(useStore.getState().revision).toBe(0);
    warnSpy.mockRestore();
  });

  it('appendElementsNoHistory refuses nodes whose id already exists (and in-batch dupes)', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const stubs = makeStubs({
      ...baseCanvas,
      nodes_json: [{ id: 'shot-1', type: 'shot', position: { x: 0, y: 0 }, data: {} }],
    });
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().appendElementsNoHistory(
      [
        { id: 'shot-1', type: 'shot', position: { x: 9, y: 9 }, data: {} }, // exists → dropped
        { id: 'shot-2', type: 'shot', position: { x: 0, y: 0 }, data: {} }, // new → kept
        { id: 'shot-2', type: 'shot', position: { x: 5, y: 5 }, data: {} }, // in-batch dupe → dropped
      ],
      [],
    );

    const ids = (useStore.getState().nodes as Array<{ id: string }>).map((n) => n.id);
    expect(ids).toEqual(['shot-1', 'shot-2']);
    warnSpy.mockRestore();
  });

  it('dedupeNodes collapses only the listed ids, keeps first occurrence, and marks dirty', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    // Seed duplicates directly (simulating a poisoned in-memory set).
    useStore.setState({
      nodes: [
        { id: 'a', type: 'shot', position: { x: 1, y: 0 }, data: {} },
        { id: 'a', type: 'shot', position: { x: 2, y: 0 }, data: {} },
        { id: 'b', type: 'shot', position: { x: 3, y: 0 }, data: {} },
        { id: 'b', type: 'shot', position: { x: 4, y: 0 }, data: {} },
      ] as never,
    });
    const revBefore = useStore.getState().revision;

    useStore.getState().dedupeNodes(['a']);

    const nodes = useStore.getState().nodes as Array<{ id: string; position: { x: number } }>;
    expect(nodes.map((n) => n.id)).toEqual(['a', 'b', 'b']);
    expect(nodes[0].position.x).toBe(1);
    // Healing persists through the normal save path → must mark dirty.
    expect(useStore.getState().revision).toBe(revBefore + 1);

    // No listed dupes → exact no-op (no dirty bump).
    const revAfter = useStore.getState().revision;
    useStore.getState().dedupeNodes(['a']);
    expect(useStore.getState().revision).toBe(revAfter);
  });
});
