// features/canvas-core/smart/loopRun.test.ts
// Store-level wiring for the from-loop run (Phase 1 G3b): startLoopRun
// drives runLoopCascade against the live canvas store — prompt statuses
// patch through, every round lands in a per-round output slot (created
// right of the tail prompt, reused on re-runs), and stop is cooperative
// via the loop-run store.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { useLoopRunStore } from './loopRunStore';
import { startLoopRun } from './loopRun';
import type { RunnerContext, RunnerResult } from './runner';
import type { CanvasNode } from '../types';

const LOOP: CanvasNode = {
  id: 'loop1',
  type: 'loop',
  position: { x: 0, y: 0 },
  data: { mode: 'serial', label: '', rounds: 2, round_start: 1, prompts: ['第《计数》轮'] },
};
const PROMPT: CanvasNode = {
  id: 'p1',
  type: 'prompt',
  position: { x: 400, y: 100 },
  data: { body: 'base', provider_slug: 'default', agent_id: null, run_status: 'idle', resource_refs: [] },
};
const EDGE = { id: 'e1', source: 'loop1', target: 'p1', sourceHandle: null, targetHandle: null };

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: [LOOP, PROMPT],
    connections: [EDGE],
    selection: [],
  });
}

const okCaller = async (ctx: RunnerContext): Promise<RunnerResult> => ({
  ok: true,
  text: `gen:${ctx.body.slice(0, 8)}`,
  error: null,
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  useLoopRunStore.getState().resetAll();
  vi.restoreAllMocks();
});

describe('startLoopRun', () => {
  it('runs the rounds, patches statuses and creates one output slot per round', async () => {
    seed();
    useLoopRunStore.getState().setRunnerOverride(okCaller);

    await startLoopRun('loop1');

    const state = useCanvasCoreStore.getState();
    const prompt = state.nodes.find((n) => (n as CanvasNode).id === 'p1') as CanvasNode;
    expect((prompt.data as { run_status: string }).run_status).toBe('succeeded');

    const slots = state.nodes.filter(
      (n) => ((n as Record<string, unknown>).data as { loop_slot?: unknown })?.loop_slot !== undefined,
    ) as Array<Record<string, unknown>>;
    expect(slots).toHaveLength(2);
    const rounds = slots
      .map((s) => ((s.data as { loop_slot: { round_index: number } }).loop_slot).round_index)
      .sort();
    expect(rounds).toEqual([1, 2]);
    // Slot preview carries the round result text.
    const previews = slots.map((s) => (s.data as { preview_text: string }).preview_text);
    expect(previews.every((p) => p.includes('gen:'))).toBe(true);

    // Each slot is wired from the tail prompt.
    const conns = state.connections as Array<Record<string, unknown>>;
    const slotIds = new Set(slots.map((s) => s.id));
    const wired = conns.filter((c) => c.source === 'p1' && slotIds.has(c.target as string));
    expect(wired).toHaveLength(2);

    // Run state cleaned up.
    expect(useLoopRunStore.getState().running['loop1']).toBeUndefined();
  });

  it('reuses the round slots on a second run instead of duplicating', async () => {
    seed();
    useLoopRunStore.getState().setRunnerOverride(okCaller);

    await startLoopRun('loop1');
    await startLoopRun('loop1');

    const slots = useCanvasCoreStore
      .getState()
      .nodes.filter(
        (n) => ((n as Record<string, unknown>).data as { loop_slot?: unknown })?.loop_slot !== undefined,
      );
    expect(slots).toHaveLength(2);
  });

  it('ignores a second start while the loop is already running', async () => {
    seed();
    let calls = 0;
    useLoopRunStore.getState().setRunnerOverride(async () => {
      calls += 1;
      await new Promise((r) => setTimeout(r, 20));
      return { ok: true, text: 'x', error: null };
    });

    const first = startLoopRun('loop1');
    await startLoopRun('loop1'); // no-op — already running
    await first;

    expect(calls).toBe(2); // 2 rounds × 1 prompt, once
  });

  it('leaves the undo history untouched (slots are operational output, not user edits)', async () => {
    seed();
    useLoopRunStore.getState().setRunnerOverride(okCaller);

    await startLoopRun('loop1');

    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
  });

  it('does not create a slot for a failed round', async () => {
    seed();
    useLoopRunStore.getState().setRunnerOverride(async () => ({
      ok: false,
      text: '',
      error: 'boom',
    }));

    await startLoopRun('loop1');

    const slots = useCanvasCoreStore
      .getState()
      .nodes.filter(
        (n) => ((n as Record<string, unknown>).data as { loop_slot?: unknown })?.loop_slot !== undefined,
      );
    expect(slots).toHaveLength(0);
  });

  it('stops writing when the store has switched to another canvas mid-run', async () => {
    seed();
    useCanvasCoreStore.setState({ canvasId: 'canvas-A' });
    const OTHER_NODES: CanvasNode[] = [
      { id: 'x1', type: 'shot', position: { x: 0, y: 0 }, data: {} },
    ];
    useLoopRunStore.getState().setRunnerOverride(async () => {
      // Simulate navigation to another canvas while round 1 is in flight.
      useCanvasCoreStore.setState({ canvasId: 'canvas-B', nodes: OTHER_NODES, connections: [] });
      return { ok: true, text: 'late', error: null };
    });

    await startLoopRun('loop1');

    const state = useCanvasCoreStore.getState();
    // Canvas B must not receive slots, edges, or status patches.
    expect(state.nodes).toHaveLength(1);
    expect(state.connections).toHaveLength(0);
    expect(useLoopRunStore.getState().running['loop1']).toBeUndefined();
  });

  it('gen rounds land images[] in the round slot instead of text', async () => {
    seed();
    useCanvasCoreStore.setState({
      nodes: [
        { ...LOOP, data: { ...(LOOP.data as object), rounds: 1 } } as CanvasNode,
        {
          ...PROMPT,
          data: { ...(PROMPT.data as object), gen: { kind: 'image', model: '', count: 2 } },
        } as CanvasNode,
      ],
    });
    useLoopRunStore.getState().setRunnerOverride(async () => ({
      ok: true,
      text: '/gm/1/cover\n/gm/2/cover',
      error: null,
      urls: ['/gm/1/cover', '/gm/2/cover'],
      media_kind: 'image',
    }));

    await startLoopRun('loop1');

    const slot = useCanvasCoreStore
      .getState()
      .nodes.find(
        (n) => ((n as Record<string, unknown>).data as { loop_slot?: unknown })?.loop_slot,
      ) as Record<string, unknown> | undefined;
    expect(slot).toBeTruthy();
    const data = slot!.data as Record<string, unknown>;
    expect(data.kind).toBe('image');
    expect(data.images).toEqual([
      { url: '/gm/1/cover', kind: 'image' },
      { url: '/gm/2/cover', kind: 'image' },
    ]);
  });

  it('requestStop halts after the in-flight round', async () => {
    seed();
    useCanvasCoreStore.setState({
      nodes: [
        { ...LOOP, data: { ...(LOOP.data as object), rounds: 5 } } as CanvasNode,
        PROMPT,
      ],
    });
    let calls = 0;
    useLoopRunStore.getState().setRunnerOverride(async () => {
      calls += 1;
      if (calls === 1) useLoopRunStore.getState().requestStop('loop1');
      await new Promise((r) => setTimeout(r, 5));
      return { ok: true, text: 'x', error: null };
    });

    await startLoopRun('loop1');

    expect(calls).toBe(1);
    expect(useLoopRunStore.getState().running['loop1']).toBeUndefined();
  });
});
