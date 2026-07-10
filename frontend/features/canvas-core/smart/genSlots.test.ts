// features/canvas-core/smart/genSlots.test.ts
// Per-prompt generation output slots (G4-F1): each result URL lands in an
// output node right of the prompt, tagged data.gen_slot {node_id, index} and
// reused on re-runs (mirrors the loop slots' contract). History-free append —
// operational output must not evict the undo stack.

import { afterEach, describe, expect, it } from 'vitest';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { upsertGenerationSlots } from './genSlots';
import type { CanvasNode } from '../types';

const PROMPT: CanvasNode = {
  id: 'p1',
  type: 'prompt',
  position: { x: 100, y: 50 },
  data: { body: 'x', provider_slug: '', agent_id: null, run_status: 'idle', resource_refs: [] },
};

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [PROMPT],
    connections: [],
    selection: [],
  });
}

afterEach(() => useCanvasCoreStore.getState().reset());

describe('upsertGenerationSlots', () => {
  it('creates one image slot per url, wired from the prompt', () => {
    seed();
    upsertGenerationSlots('p1', ['/gm/1/cover', '/gm/2/cover'], 'image');

    const state = useCanvasCoreStore.getState();
    const slots = state.nodes.filter(
      (n) => ((n as Record<string, unknown>).data as { gen_slot?: unknown })?.gen_slot,
    ) as Array<Record<string, unknown>>;
    expect(slots).toHaveLength(2);
    expect((slots[0].data as { kind: string }).kind).toBe('image');
    expect((slots[0].data as { preview_url: string }).preview_url).toBe('/gm/1/cover');
    const conns = state.connections as Array<Record<string, unknown>>;
    expect(conns.filter((c) => c.source === 'p1')).toHaveLength(2);
  });

  it('reuses slots by index on a re-run instead of duplicating', () => {
    seed();
    upsertGenerationSlots('p1', ['/gm/1/cover'], 'image');
    upsertGenerationSlots('p1', ['/gm/9/cover'], 'image');

    const state = useCanvasCoreStore.getState();
    const slots = state.nodes.filter(
      (n) => ((n as Record<string, unknown>).data as { gen_slot?: unknown })?.gen_slot,
    ) as Array<Record<string, unknown>>;
    expect(slots).toHaveLength(1);
    expect((slots[0].data as { preview_url: string }).preview_url).toBe('/gm/9/cover');
  });

  it('does not touch undo history', () => {
    seed();
    upsertGenerationSlots('p1', ['/gm/1/cover'], 'image');
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
  });

  it('no-ops when the prompt node is gone', () => {
    seed();
    upsertGenerationSlots('missing', ['/gm/1/cover'], 'image');
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});
