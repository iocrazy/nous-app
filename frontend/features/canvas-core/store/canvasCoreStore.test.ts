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
