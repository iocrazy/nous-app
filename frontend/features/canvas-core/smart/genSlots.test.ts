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
  it('lands ALL urls in ONE slot as images[] (Infinite: count N = one node, N images)', () => {
    seed();
    upsertGenerationSlots('p1', ['/gm/1/cover', '/gm/2/cover'], 'image');

    const state = useCanvasCoreStore.getState();
    const slots = state.nodes.filter(
      (n) => ((n as Record<string, unknown>).data as { gen_slot?: unknown })?.gen_slot,
    ) as Array<Record<string, unknown>>;
    expect(slots).toHaveLength(1);
    const data = slots[0].data as Record<string, unknown>;
    expect(data.kind).toBe('image');
    expect(data.images).toEqual([
      { url: '/gm/1/cover', kind: 'image' },
      { url: '/gm/2/cover', kind: 'image' },
    ]);
    expect(data.preview_url).toBe('/gm/1/cover');
    const conns = state.connections as Array<Record<string, unknown>>;
    expect(conns.filter((c) => c.source === 'p1')).toHaveLength(1);
  });

  it('re-run reuses the slot and archives the previous images into history', () => {
    seed();
    upsertGenerationSlots('p1', ['/gm/1/cover'], 'image');
    upsertGenerationSlots('p1', ['/gm/9/cover'], 'image');

    const state = useCanvasCoreStore.getState();
    const slots = state.nodes.filter(
      (n) => ((n as Record<string, unknown>).data as { gen_slot?: unknown })?.gen_slot,
    ) as Array<Record<string, unknown>>;
    expect(slots).toHaveLength(1);
    const data = slots[0].data as Record<string, unknown>;
    expect((data.images as Array<{ url: string }>)[0].url).toBe('/gm/9/cover');

    const history = state.nodes.filter(
      (n) => ((n as Record<string, unknown>).data as { history_for?: string })?.history_for,
    ) as Array<Record<string, unknown>>;
    expect(history).toHaveLength(1);
    expect((history[0].data as { images: Array<{ url: string }> }).images).toEqual([
      { url: '/gm/1/cover', kind: 'image' },
    ]);
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

// ── Progressive placeholders (P0-3) ─────────────────────────────────────────

import {
  appendGenerationResults,
  beginGenerationSlot,
  settleGenerationSlot,
} from './genSlots';

const slotOf = () => {
  const state = useCanvasCoreStore.getState();
  const slot = state.nodes.find(
    (n) => ((n as Record<string, unknown>).data as { gen_slot?: unknown })?.gen_slot,
  ) as Record<string, unknown> | undefined;
  return slot?.data as
    | { images?: Array<{ url: string }>; gen_pending?: number; gen_failed?: number }
    | undefined;
};

describe('progressive generation placeholders (P0-3)', () => {
  it('beginGenerationSlot creates the slot with N pending cells at dispatch', () => {
    seed();
    beginGenerationSlot('p1', 3, 'image');
    const data = slotOf();
    expect(data?.gen_pending).toBe(3);
    expect(data?.images ?? []).toHaveLength(0);
  });

  it('appendGenerationResults fills cells first-done-first-shown', () => {
    seed();
    beginGenerationSlot('p1', 2, 'image');
    appendGenerationResults('p1', ['/gm/2/cover'], 'image');
    let data = slotOf();
    expect(data?.images?.map((i) => i.url)).toEqual(['/gm/2/cover']);
    expect(data?.gen_pending).toBe(1);
    appendGenerationResults('p1', ['/gm/1/cover'], 'image');
    data = slotOf();
    expect(data?.images?.map((i) => i.url)).toEqual(['/gm/2/cover', '/gm/1/cover']);
    expect(data?.gen_pending).toBe(0);
  });

  it('re-running archives the previous batch exactly once', () => {
    seed();
    beginGenerationSlot('p1', 1, 'image');
    appendGenerationResults('p1', ['/gm/old/cover'], 'image');
    beginGenerationSlot('p1', 2, 'image');
    const data = slotOf();
    expect(data?.images ?? []).toHaveLength(0);
    expect(data?.gen_pending).toBe(2);
    const history = useCanvasCoreStore
      .getState()
      .nodes.find(
        (n) =>
          ((n as Record<string, unknown>).data as { history_for?: string })
            ?.history_for,
      ) as Record<string, unknown> | undefined;
    expect(history).toBeTruthy();
    const hImages = (history!.data as { images: Array<{ url: string }> }).images;
    expect(hImages.map((i) => i.url)).toContain('/gm/old/cover');
  });

  it('settleGenerationSlot burns pending into failed; clearPending sweeps the rest', () => {
    seed();
    beginGenerationSlot('p1', 3, 'image');
    settleGenerationSlot('p1', { failed: 1 });
    let data = slotOf();
    expect(data?.gen_pending).toBe(2);
    expect(data?.gen_failed).toBe(1);
    settleGenerationSlot('p1', { clearPending: true });
    data = slotOf();
    expect(data?.gen_pending).toBe(0);
    expect(data?.gen_failed).toBe(1);
  });
});

// ── Recover marks (P1-13) ───────────────────────────────────────────────────

import { markGenerationRecover, resolveGenerationRecover } from './genSlots';

describe('generation recover marks (P1-13)', () => {
  it('markGenerationRecover swaps a pending cell for a recover entry', () => {
    seed();
    beginGenerationSlot('p1', 2, 'image');
    markGenerationRecover('p1', 't1');
    const data = slotOf() as Record<string, unknown> | undefined;
    expect(data?.gen_pending).toBe(1);
    expect(data?.gen_recover).toEqual(['t1']);
  });

  it('resolve with a url appends the image WITHOUT eating sibling pending cells', () => {
    seed();
    beginGenerationSlot('p1', 3, 'image');
    markGenerationRecover('p1', 't1');
    resolveGenerationRecover('p1', 't1', { url: '/gm/9/cover', kind: 'image' });
    const data = slotOf() as
      | { gen_pending?: number; gen_recover?: string[]; images?: Array<{ url: string }> }
      | undefined;
    expect(data?.gen_recover).toEqual([]);
    expect(data?.images?.map((i) => i.url)).toEqual(['/gm/9/cover']);
    // 3 dispatched − 1 recovered = 2 siblings still pending, untouched.
    expect(data?.gen_pending).toBe(2);
  });

  it('resolve as failed burns the mark into the failed count', () => {
    seed();
    beginGenerationSlot('p1', 1, 'image');
    markGenerationRecover('p1', 't1');
    resolveGenerationRecover('p1', 't1', { url: null, kind: 'image' });
    const data = slotOf() as Record<string, unknown> | undefined;
    expect(data?.gen_recover).toEqual([]);
    expect(data?.gen_failed).toBe(1);
  });
});

// ── Requested ratio stamp (canvas fluency Task 7) ───────────────────────────
// The output node has to reserve the right BOX before any bytes arrive, and
// the ratio the user asked for lives on the PROMPT. Copying it onto the slot
// at dispatch keeps the node view data-driven: it never walks the graph back
// to its prompt (that would be a store subscription inside a memo-wrapped
// node, i.e. exactly the per-node re-render Task 4 removed).

function seedGenPrompt(gen: Record<string, unknown> | null): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [{ ...PROMPT, data: { ...(PROMPT.data as object), gen } } as CanvasNode],
    connections: [],
    selection: [],
  });
}

const ratioOf = () =>
  (slotOf() as { gen_ratio?: string | null } | undefined)?.gen_ratio;

describe('beginGenerationSlot stamps the requested ratio', () => {
  it("copies the prompt's image ratio onto the slot at dispatch", () => {
    seedGenPrompt({ kind: 'image', model: '', ratio: '16:9' });
    beginGenerationSlot('p1', 2, 'image');
    expect(ratioOf()).toBe('16:9');
  });

  it('uses the video aspect for a video run', () => {
    seedGenPrompt({ kind: 'video', model: '', aspect: '9:16' });
    beginGenerationSlot('p1', 1, 'video');
    expect(ratioOf()).toBe('9:16');
  });

  it('is null when the prompt has no generation settings', () => {
    seedGenPrompt(null);
    beginGenerationSlot('p1', 1, 'image');
    expect(ratioOf() ?? null).toBeNull();
  });

  it('refreshes on a re-run — a stale ratio would reserve the wrong box', () => {
    seedGenPrompt({ kind: 'image', model: '', ratio: '16:9' });
    beginGenerationSlot('p1', 1, 'image');
    expect(ratioOf()).toBe('16:9');
    // The user changes the ratio and runs again: the SAME slot is reused, so
    // the stamp has to be rewritten rather than left at the first run's value.
    useCanvasCoreStore.getState().patchNode('p1', {
      data: { gen: { kind: 'image', model: '', ratio: '3:4' } },
    });
    beginGenerationSlot('p1', 1, 'image');
    expect(ratioOf()).toBe('3:4');
  });
});

describe('beginGenerationSlot prefers the ratio the dispatch actually used', () => {
  // The prompt's raw value is not the request for the default image path:
  // 'auto' (and unset) are resolved by measuring the source, and only the
  // runner knows the answer. So the dispatched value wins, and the prompt's
  // own value is only the fallback for callers that have none.

  it('stamps the dispatched ratio over the prompt saying auto', () => {
    seedGenPrompt({ kind: 'image', model: '', ratio: 'auto' });
    beginGenerationSlot('p1', 1, 'image', '16:9');
    expect(ratioOf()).toBe('16:9');
  });

  it('stamps it on a re-run too — the slot is reused', () => {
    seedGenPrompt({ kind: 'image', model: '', ratio: 'auto' });
    beginGenerationSlot('p1', 1, 'image', '16:9');
    beginGenerationSlot('p1', 1, 'image', '3:4');
    expect(ratioOf()).toBe('3:4');
  });

  it("falls back to the prompt's own value when the dispatch sent none", () => {
    seedGenPrompt({ kind: 'image', model: '', ratio: '21:9' });
    beginGenerationSlot('p1', 1, 'image', null);
    expect(ratioOf()).toBe('21:9');
  });

  it('falls back for callers that pass no ratio at all (recover marks)', () => {
    seedGenPrompt({ kind: 'image', model: '', ratio: '21:9' });
    beginGenerationSlot('p1', 1, 'image');
    expect(ratioOf()).toBe('21:9');
  });
});
