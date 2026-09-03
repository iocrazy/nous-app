/**
 * Read-only latch (2026-08-12 production incident).
 *
 * A viewer-role member opening a team canvas gets HTTP 403 on `PUT
 * /canvases/{id}` — including for the autosave the LOAD ITSELF triggers
 * (`applyServerRow` sanitizes `nodes_json`, the storyboard reconcile patches
 * nodes). Before this latch that 403 landed in the generic catch as
 * `saveStatus: 'error'`, the red "Save failed" badge lit up, and every
 * subsequent change re-armed the 500ms debounce — two PUTs inside the same
 * second were observed on the real row. Permission is a standing fact, not a
 * retryable failure.
 *
 * Fixtures use the real wire shape of `GET /api/v1/canvases/{id}`: the
 * canvases router stringifies its Snowflake ids (`str(out["id"])`), so the
 * row's `id`/`project_id` are JSON strings here — deliberately unlike the
 * scenes/shots routers, which return them as JSON numbers.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../../services/apiClient';
import type { Canvas, CanvasSaveResult, CanvasUpdatePayload } from '../types';
import { createCanvasCoreStore } from './canvasCoreStore';

const baseCanvas: Canvas = {
  id: '337610660408263',
  project_id: '337610660408111',
  name: 'Episode 1 storyboard',
  kind: 'storyboard',
  viewport_json: { x: 181.47, y: 106.68, zoom: 0.514 },
  nodes_json: [],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-08-12T09:00:00+00:00',
  created_at: '2026-08-01T09:00:00+00:00',
  updated_at: '2026-08-12T09:00:00+00:00',
  created_by: null,
};

/** Server stub whose PUT is refused exactly the way `saveCanvas` refuses it:
 *  a thrown `ApiError` carrying `status: 403` (the router's own body is
 *  `{"detail": "..."}`, reduced to a message by the service). */
function makeForbiddenStubs() {
  const loadImpl = vi.fn(async (): Promise<Canvas> => ({ ...baseCanvas }));
  const saveImpl = vi.fn(
    async (
      _canvasId: string,
      _payload: CanvasUpdatePayload,
    ): Promise<CanvasSaveResult> => {
      throw new ApiError('Not authorized to edit this canvas', 403);
    },
  );
  return { loadImpl, saveImpl };
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/**
 * Upfront permission (2026-08-13). The 403 latch above is the BACKSTOP;
 * the load response's `can_edit` is now the primary source, so a viewer's
 * session never sends the doomed PUT at all.
 */
describe('canvasCoreStore — can_edit on load', () => {
  function stubs(row: Canvas) {
    return {
      loadImpl: vi.fn(async (): Promise<Canvas> => ({ ...row })),
      saveImpl: vi.fn(
        async (): Promise<CanvasSaveResult> => ({ ok: true, canvas: { ...row } }),
      ),
    };
  }

  it('can_edit:false → read-only, and ZERO PUTs for the whole session', async () => {
    // The headline regression: before this, loading as a viewer cost one
    // guaranteed-403 PUT (the load-time sanitize/reconcile autosave) just
    // to discover a fact the server could have stated in the GET.
    const s = stubs({ ...baseCanvas, can_edit: false });
    const useStore = createCanvasCoreStore({ ...s, debounceMs: 500 });

    await useStore.getState().loadCanvas('337610660408263');
    expect(useStore.getState().readOnly).toBe(true);

    // Every channel that would normally schedule or force a save.
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    useStore.getState().patchNode('shot-1', { data: { run_status: 'running' } });
    useStore.getState().setViewportSettled({ x: 7, y: 7, zoom: 1.25 });
    await vi.advanceTimersByTimeAsync(5000);
    await useStore.getState().flushSave();

    expect(s.saveImpl).toHaveBeenCalledTimes(0);
  });

  it('can_edit:true → writable, saves normally', async () => {
    const s = stubs({ ...baseCanvas, can_edit: true });
    const useStore = createCanvasCoreStore({ ...s, debounceMs: 0 });

    await useStore.getState().loadCanvas('337610660408263');
    expect(useStore.getState().readOnly).toBe(false);

    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);

    expect(s.saveImpl).toHaveBeenCalledTimes(1);
  });

  it('a load with no can_edit stays writable (older backend → 403 backstop)', async () => {
    const s = stubs(baseCanvas); // field absent entirely
    const useStore = createCanvasCoreStore({ ...s, debounceMs: 0 });

    await useStore.getState().loadCanvas('337610660408263');

    expect(useStore.getState().readOnly).toBe(false);
  });

  /** Serves `rows` in order, one per `loadCanvas` call. */
  function sequencedLoad(rows: Canvas[]) {
    let call = 0;
    return vi.fn(async (): Promise<Canvas> => ({ ...rows[call++] }));
  }

  it('a read-only canvas does not leak its lock onto the NEXT canvas loaded', async () => {
    // The store is a module-level singleton reused across mounts.
    const loadImpl = sequencedLoad([
      { ...baseCanvas, id: 'ro', can_edit: false },
      { ...baseCanvas, id: 'rw', can_edit: true },
    ]);
    const saveImpl = vi.fn(
      async (): Promise<CanvasSaveResult> => ({ ok: true, canvas: { ...baseCanvas } }),
    );
    const useStore = createCanvasCoreStore({ loadImpl, saveImpl, debounceMs: 0 });

    await useStore.getState().loadCanvas('ro');
    expect(useStore.getState().readOnly).toBe(true);

    await useStore.getState().loadCanvas('rw');
    expect(useStore.getState().readOnly).toBe(false);

    useStore.getState().setNodes([{ id: 'n1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(saveImpl).toHaveBeenCalledTimes(1);
  });

  it('the reverse leak too: a writable canvas does not unlock the NEXT read-only one', async () => {
    const loadImpl = sequencedLoad([
      { ...baseCanvas, id: 'rw', can_edit: true },
      { ...baseCanvas, id: 'ro', can_edit: false },
    ]);
    const saveImpl = vi.fn(
      async (): Promise<CanvasSaveResult> => ({ ok: true, canvas: { ...baseCanvas } }),
    );
    const useStore = createCanvasCoreStore({ loadImpl, saveImpl, debounceMs: 0 });

    await useStore.getState().loadCanvas('rw');
    await useStore.getState().loadCanvas('ro');

    expect(useStore.getState().readOnly).toBe(true);
    useStore.getState().setNodes([{ id: 'n1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(saveImpl).toHaveBeenCalledTimes(0);
  });

  it('a realtime rebase carries no permission statement — the lock survives it', async () => {
    // Supabase Realtime broadcasts the raw `canvases` row: no caller, so
    // no can_edit. Treating "silent" as "writable" would unlock a viewer's
    // surface on every broadcast.
    const s = stubs({ ...baseCanvas, can_edit: false });
    const useStore = createCanvasCoreStore({ ...s, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');
    expect(useStore.getState().readOnly).toBe(true);

    useStore.getState().applyRemoteUpdate({
      ...baseCanvas, // no can_edit — exactly what Realtime delivers
      base_updated_at: '2026-08-12T10:00:00+00:00',
      nodes_json: [{ id: 'shot-remote', position: { x: 1, y: 1 } }],
    });

    expect(useStore.getState().readOnly).toBe(true);
    expect(useStore.getState().nodes).toHaveLength(1); // the rebase still applied
    useStore.getState().setNodes([{ id: 'x', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(s.saveImpl).toHaveBeenCalledTimes(0);
  });
});

describe('canvasCoreStore — read-only latch on 403', () => {
  it('starts writable', async () => {
    const useStore = createCanvasCoreStore({ ...makeForbiddenStubs(), debounceMs: 0 });
    expect(useStore.getState().readOnly).toBe(false);
    await useStore.getState().loadCanvas('337610660408263');
    expect(useStore.getState().readOnly).toBe(false);
  });

  it('a 403 save sets readOnly and leaves the save status CLEAN (no "Save failed")', async () => {
    const stubs = makeForbiddenStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');

    useStore.getState().setNodes([{ id: 'shot-337610660408299', position: { x: 2240, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);

    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    const s = useStore.getState();
    expect(s.readOnly).toBe(true);
    expect(s.saveStatus).toBe('idle');
    expect(s.saveError).toBeNull();
  });

  it('no further PUT is ever sent — later edits neither schedule nor flush a save', async () => {
    const stubs = makeForbiddenStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 500 });
    await useStore.getState().loadCanvas('337610660408263');

    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(500);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    expect(useStore.getState().readOnly).toBe(true);

    // Every mutation channel that normally re-arms the debounce.
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 10, y: 10 } }]);
    useStore.getState().patchNode('shot-1', { data: { run_status: 'running' } });
    useStore.getState().setViewportSettled({ x: 7, y: 7, zoom: 1.25 });
    await vi.advanceTimersByTimeAsync(5000);

    // …and the direct, non-debounced path (surface unmount / route leave).
    await useStore.getState().flushSave();

    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
  });

  it('markDirty stops bumping revision, so a realtime row rebases instead of raising an unresolvable conflict', async () => {
    const stubs = makeForbiddenStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().readOnly).toBe(true);

    const revisionAfterLatch = useStore.getState().revision;
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 99, y: 99 } }]);
    expect(useStore.getState().revision).toBe(revisionAfterLatch);
    // Local edit still applies to the in-memory doc — it just never lands.
    expect(useStore.getState().nodes).toHaveLength(1);

    useStore.getState().applyRemoteUpdate({
      ...baseCanvas,
      base_updated_at: '2026-08-12T10:00:00+00:00',
      nodes_json: [{ id: 'shot-remote', position: { x: 1, y: 1 } }],
    });
    const s = useStore.getState();
    expect(s.conflict).toBeNull();
    expect(s.nodes.map((n) => (n as { id: string }).id)).toEqual(['shot-remote']);
  });

  it('a fresh load clears the latch — the singleton store is reused across canvases', async () => {
    const stubs = makeForbiddenStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().readOnly).toBe(true);

    await useStore.getState().loadCanvas('337610660408263');
    expect(useStore.getState().readOnly).toBe(false);

    // …and saving genuinely resumes (the latch was the only thing muting it).
    useStore.getState().setNodes([{ id: 'shot-2', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(2);
  });

  it('reset() clears the latch too', async () => {
    const stubs = makeForbiddenStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().readOnly).toBe(true);

    useStore.getState().reset();
    expect(useStore.getState().readOnly).toBe(false);
  });

  it('a non-403 failure keeps the old retryable behaviour (error status, no latch)', async () => {
    const loadImpl = vi.fn(async (): Promise<Canvas> => ({ ...baseCanvas }));
    const saveImpl = vi.fn(async (): Promise<CanvasSaveResult> => {
      throw new ApiError('Internal Server Error', 500);
    });
    const useStore = createCanvasCoreStore({ loadImpl, saveImpl, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);

    const s = useStore.getState();
    expect(s.readOnly).toBe(false);
    expect(s.saveStatus).toBe('error');
    expect(s.saveError).toBe('Internal Server Error');

    // Still retryable — a later edit schedules another attempt.
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 1, y: 1 } }]);
    await vi.advanceTimersByTimeAsync(0);
    expect(saveImpl).toHaveBeenCalledTimes(2);
  });

  it('a plain network error (no status field) is not mistaken for a 403', async () => {
    const loadImpl = vi.fn(async (): Promise<Canvas> => ({ ...baseCanvas }));
    const saveImpl = vi.fn(async (): Promise<CanvasSaveResult> => {
      throw new TypeError('Failed to fetch');
    });
    const useStore = createCanvasCoreStore({ loadImpl, saveImpl, debounceMs: 0 });
    await useStore.getState().loadCanvas('337610660408263');
    useStore.getState().setNodes([{ id: 'shot-1', position: { x: 0, y: 0 } }]);
    await vi.advanceTimersByTimeAsync(0);

    expect(useStore.getState().readOnly).toBe(false);
    expect(useStore.getState().saveStatus).toBe('error');
  });
});
