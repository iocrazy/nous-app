// What must happen the moment a generation is DISPATCHED — for every entry
// point, not just one of them.
//
// The canvas can start a run from three places: the composer's Run/Cascade,
// the Run button inside a prompt node, and the attached panel's Run. Only the
// composer created the output slot at dispatch time; the other two waited for
// results, so a running prompt sat there with nothing beside it and the user
// could not tell anything was happening on the canvas.
//
// The fix is not "add the missing call in the second place" — that is how the
// two drifted apart to begin with. Both now share one effect, and the last
// test here fails if a future entry point rolls its own again.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { onGenerationDispatched } from './dispatchEffects';

const PROMPT: CanvasNode = {
  id: 'p1',
  type: 'prompt',
  position: { x: 100, y: 200 },
  data: {
    body: 'a cat',
    provider_slug: 'jimeng',
    agent_id: null,
    run_status: 'running',
    resource_refs: [],
    gen: { kind: 'image', model: 'jimeng-4', ratio: '1:1', count: 2 },
  },
} as unknown as CanvasNode;

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({ kind: 'smart', canvasId: '9', nodes: [PROMPT], connections: [] });
}

/** The output slot belonging to a prompt, identified the way the store tags it. */
const slotOf = (promptId: string) =>
  useCanvasCoreStore
    .getState()
    .nodes.find(
      (n) =>
        ((n as unknown as { data?: { gen_slot?: { node_id?: string } } }).data?.gen_slot
          ?.node_id ?? null) === promptId,
    );

beforeEach(seed);

describe('onGenerationDispatched', () => {
  it('creates the output slot immediately, before any result arrives', () => {
    expect(slotOf('p1'), 'a slot existed before dispatch').toBeUndefined();
    onGenerationDispatched('p1', 2, 'image', ['t1', 't2']);
    expect(slotOf('p1'), 'no slot appeared at dispatch time').toBeDefined();
  });

  it('sizes the placeholder to the requested count', () => {
    onGenerationDispatched('p1', 3, 'image', ['t1', 't2', 't3']);
    const data = (slotOf('p1') as unknown as { data: { gen_pending?: number } }).data;
    expect(data.gen_pending).toBe(3);
  });

  it('places the slot to the RIGHT of its prompt', () => {
    onGenerationDispatched('p1', 1, 'image', ['t1']);
    const slot = slotOf('p1') as unknown as { position: { x: number; y: number } };
    expect(
      slot.position.x,
      'slot is not to the right — the run reads as going nowhere',
    ).toBeGreaterThan(100);
    expect(slot.position.y).toBe(200);
  });

  it('wires prompt → slot so the run has a visible direction', () => {
    onGenerationDispatched('p1', 1, 'image', ['t1']);
    const slotId = String((slotOf('p1') as unknown as { id: string }).id);
    const edge = useCanvasCoreStore
      .getState()
      .connections.find((c) => c.source === 'p1' && c.target === slotId);
    expect(edge, 'no edge from the prompt to its pending slot').toBeDefined();
  });

  it('persists the task ids so a reload can resume the batch', () => {
    onGenerationDispatched('p1', 2, 'image', ['t1', 't2']);
    const prompt = useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === 'p1');
    const data = (prompt as unknown as { data: Record<string, unknown> }).data;
    expect(data.gen_tasks ?? data.pending_gen_tasks, 'task ids were not persisted').toBeTruthy();
  });

  it('is idempotent for a re-run — one slot, not two', () => {
    onGenerationDispatched('p1', 1, 'image', ['t1']);
    onGenerationDispatched('p1', 1, 'image', ['t2']);
    const slots = useCanvasCoreStore
      .getState()
      .nodes.filter(
        (n) =>
          ((n as unknown as { data?: { gen_slot?: { node_id?: string } } }).data?.gen_slot
            ?.node_id ?? null) === 'p1',
      );
    expect(slots).toHaveLength(1);
  });
});

describe('every dispatch site shares the effect', () => {
  // A source scan, deliberately: the drift this fixes was one call site
  // quietly doing less than another, which no behavioural test on either
  // site alone can catch.
  const read = (rel: string) => readFileSync(join(__dirname, rel), 'utf8');

  it('no dispatch site calls beginGenerationSlot directly any more', () => {
    for (const rel of ['CanvasComposer.tsx', 'regenerate.ts']) {
      expect(
        read(rel),
        `${rel} still calls beginGenerationSlot itself — use onGenerationDispatched so every entry point behaves the same`,
      ).not.toMatch(/\bbeginGenerationSlot\s*\(/);
    }
  });

  it('both dispatch sites go through onGenerationDispatched', () => {
    for (const rel of ['CanvasComposer.tsx', 'regenerate.ts']) {
      expect(read(rel), `${rel} does not use the shared dispatch effect`).toMatch(
        /onGenerationDispatched/,
      );
    }
  });
});
