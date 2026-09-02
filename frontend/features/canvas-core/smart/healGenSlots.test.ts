/**
 * Load-time self-heal for stale generation-slot shimmer cells.
 *
 * Reported (with screenshot): "生成 16:9 的图片,后面多一个图片占用框,一直在
 * 闪烁" — the slot node persisted with `gen_pending: 1` next to a delivered
 * image, so its shimmer cell pulses forever.
 *
 * The decrement owners are all transient: the live runner's onItemSettled,
 * genResume for tasks still in the prompt's `gen_tasks` registry, and the
 * recover overlay's re-query. Once the prompt is TERMINAL and the registry
 * is EMPTY, no owner will ever come back for a leftover pending count — the
 * cell can only flicker until someone edits the node. (The exact write
 * sequence that strands it was not reproducible from the documented paths;
 * this heals the CLASS at load, same hook and same spirit as the 2026-08-12
 * duplicate-node dedupe that lives beside it.)
 *
 * The invariant, precisely: pending cells may only exist while something
 * owns them — a non-terminal run, or a non-empty resume registry. Recover
 * entries are NOT touched: they carry their own re-query UI and stay valid
 * indefinitely by design ("task not lost").
 */
import { describe, expect, it } from 'vitest';

import { healStaleGenSlots } from './healGenSlots';
import type { CanvasNode } from '../types';

function prompt(id: string, over: Record<string, unknown> = {}): CanvasNode {
  return {
    id,
    type: 'prompt',
    position: { x: 0, y: 0 },
    data: { body: 'x', run_status: 'succeeded', gen_tasks: [], ...over },
  } as unknown as CanvasNode;
}

function slot(id: string, promptId: string, over: Record<string, unknown> = {}): CanvasNode {
  return {
    id,
    type: 'output',
    position: { x: 0, y: 0 },
    data: {
      kind: 'image',
      images: [{ url: '/img.png', kind: 'image' }],
      gen_pending: 1,
      gen_failed: 0,
      gen_slot: { node_id: promptId, index: 0 },
      ...over,
    },
  } as unknown as CanvasNode;
}

const dataOf = (n: CanvasNode) => (n as unknown as { data: Record<string, unknown> }).data;

describe('healStaleGenSlots', () => {
  it('clamps pending to 0 when the prompt is terminal and the registry is empty', () => {
    // The reported state, verbatim from the stuck canvas: pending=1, images=1,
    // prompt succeeded, gen_tasks [].
    const healed = healStaleGenSlots([prompt('p1'), slot('s1', 'p1')]);
    expect(dataOf(healed[1]).gen_pending).toBe(0);
  });

  it('leaves pending alone while the run still owns it', () => {
    for (const status of ['running', 'queued'] as const) {
      const healed = healStaleGenSlots([
        prompt('p1', { run_status: status }),
        slot('s1', 'p1'),
      ]);
      expect(dataOf(healed[1]).gen_pending, `run_status=${status}`).toBe(1);
    }
  });

  it('leaves pending alone while the resume registry still owns it', () => {
    // genResume will decrement these on its own poll — healing here would
    // race it and double-count.
    const healed = healStaleGenSlots([
      prompt('p1', { gen_tasks: [{ task_id: 't1', kind: 'image' }] }),
      slot('s1', 'p1'),
    ]);
    expect(dataOf(healed[1]).gen_pending).toBe(1);
  });

  it('clamps an orphan slot whose prompt no longer exists', () => {
    // No prompt ⇒ no runner, no resume, no owner ever.
    const healed = healStaleGenSlots([slot('s1', 'p-gone')]);
    expect(dataOf(healed[0]).gen_pending).toBe(0);
  });

  it('never touches recover entries — they have their own re-query UI', () => {
    const healed = healStaleGenSlots([
      prompt('p1'),
      slot('s1', 'p1', { gen_recover: ['t9'] }),
    ]);
    expect(dataOf(healed[1]).gen_recover).toEqual(['t9']);
    expect(dataOf(healed[1]).gen_pending).toBe(0);
  });

  it('returns the SAME array reference when nothing needs healing', () => {
    // Load runs this on every canvas; a no-op must not churn node identity
    // (React Flow re-renders on new references).
    const nodes = [prompt('p1'), slot('s1', 'p1', { gen_pending: 0 })];
    expect(healStaleGenSlots(nodes)).toBe(nodes);
  });

  it('ignores nodes without a gen_slot tag entirely', () => {
    const plain = {
      id: 'o1',
      type: 'output',
      position: { x: 0, y: 0 },
      data: { images: [], gen_pending: 3 },
    } as unknown as CanvasNode;
    const nodes = [plain];
    expect(healStaleGenSlots(nodes)).toBe(nodes);
  });
});
