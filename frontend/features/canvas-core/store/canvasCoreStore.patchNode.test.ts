import { describe, expect, it, vi } from 'vitest';

import type { Canvas, CanvasSaveResult } from '../types';
import { createCanvasCoreStore } from './canvasCoreStore';

const baseCanvas: Canvas = {
  id: '4242',
  project_id: '111',
  name: 'X',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [
    { id: 'p1', type: 'prompt', data: { body: 'a', run_status: 'idle' } },
    { id: 'p2', type: 'prompt', data: { body: 'b', run_status: 'idle' } },
  ],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-06-10T12:00:00+00:00',
  created_at: '2026-06-10T12:00:00+00:00',
  updated_at: '2026-06-10T12:00:00+00:00',
  created_by: null,
};

function setup() {
  const loadImpl = vi.fn(async (): Promise<Canvas> => ({ ...baseCanvas }));
  const saveImpl = vi.fn(
    async (): Promise<CanvasSaveResult> => ({
      ok: true,
      canvas: { ...baseCanvas },
    }),
  );
  return createCanvasCoreStore({
    debounceMs: 9999,
    historyDebounceMs: 9999,
    loadImpl,
    saveImpl,
  });
}

describe('patchNode', () => {
  it('merges into data; leaves other nodes alone', async () => {
    const useStore = setup();
    await useStore.getState().loadCanvas('4242');
    useStore.getState().patchNode('p1', { data: { run_status: 'running' } });
    const nodes = useStore.getState().nodes;
    const p1 = nodes.find((n) => (n as Record<string, unknown>).id === 'p1');
    const p2 = nodes.find((n) => (n as Record<string, unknown>).id === 'p2');
    expect((p1 as Record<string, Record<string, unknown>>).data.run_status).toBe('running');
    expect((p1 as Record<string, Record<string, unknown>>).data.body).toBe('a');
    expect((p2 as Record<string, Record<string, unknown>>).data.run_status).toBe('idle');
  });

  it('non-existent id is a silent no-op', async () => {
    const useStore = setup();
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().nodes;
    useStore.getState().patchNode('nope', { data: { foo: 1 } });
    expect(useStore.getState().nodes).toBe(before);
  });

  it('does NOT push onto the undo stack (runtime state is not undoable)', async () => {
    const useStore = setup();
    await useStore.getState().loadCanvas('4242');
    expect(useStore.getState().canUndo()).toBe(false);
    useStore.getState().patchNode('p1', { data: { run_status: 'running' } });
    expect(useStore.getState().canUndo()).toBe(false);
  });

  it('bumps revision so the save debouncer picks it up', async () => {
    const useStore = setup();
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().revision;
    useStore.getState().patchNode('p1', { data: { run_status: 'running' } });
    expect(useStore.getState().revision).toBe(before + 1);
  });
});
