// features/canvas-core/smart/genResume.test.ts
// Broken-connection recovery (P1-13 — Infinite's resumeSmartPendingTasks):
// in-flight generation task ids persist on the prompt node (data.gen_tasks,
// survives refresh via nodes_json), a reload re-attaches polling, and a
// recover-marked task can be re-queried once from the slot overlay.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getGeneration = vi.fn();
const pollGeneration = vi.fn();
vi.mock('../services/canvasGenerationService', async () => {
  const actual = await vi.importActual<
    typeof import('../services/canvasGenerationService')
  >('../services/canvasGenerationService');
  return {
    ...actual,
    getGeneration: (...a: unknown[]) => getGeneration(...a),
    pollGeneration: (...a: unknown[]) => pollGeneration(...a),
  };
});

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  clearPendingGenTasks,
  persistPendingGenTasks,
  prunePendingGenTask,
  requeryRecoverTask,
  resumePendingGenerations,
} from './genResume';
import type { CanvasNode } from '../types';

const asObj = (n: unknown) => n as Record<string, unknown>;

const PROMPT: CanvasNode = {
  id: 'p1',
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: { body: 'x', provider_slug: '', agent_id: null, run_status: 'running' },
};

function seed(extraNodes: CanvasNode[] = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [PROMPT, ...extraNodes],
    connections: [],
    selection: [],
  });
}

function promptData(): Record<string, unknown> {
  const n = useCanvasCoreStore.getState().nodes.find((x) => asObj(x).id === 'p1');
  return (asObj(n).data ?? {}) as Record<string, unknown>;
}

function slotData(): Record<string, unknown> | null {
  const n = useCanvasCoreStore
    .getState()
    .nodes.find((x) => (asObj(x).data as { gen_slot?: unknown })?.gen_slot);
  return n ? ((asObj(n).data ?? {}) as Record<string, unknown>) : null;
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => useCanvasCoreStore.getState().reset());

describe('pending task persistence (P1-13)', () => {
  it('persist → prune → clear round-trip on the prompt node', () => {
    seed();
    persistPendingGenTasks('p1', ['t1', 't2'], 'image');
    expect(promptData().gen_tasks).toEqual([
      { task_id: 't1', kind: 'image' },
      { task_id: 't2', kind: 'image' },
    ]);
    prunePendingGenTask('p1', 't1');
    expect(promptData().gen_tasks).toEqual([{ task_id: 't2', kind: 'image' }]);
    clearPendingGenTasks('p1');
    expect(promptData().gen_tasks).toEqual([]);
  });
});

describe('resumePendingGenerations (P1-13)', () => {
  it('re-polls persisted tasks and lands results in the slot', async () => {
    seed();
    persistPendingGenTasks('p1', ['t1'], 'image');
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/cover' },
    });

    await resumePendingGenerations();

    const slot = slotData();
    expect(slot?.images).toEqual([{ url: '/gm/1/cover', kind: 'image' }]);
    expect(promptData().gen_tasks).toEqual([]);
    expect(promptData().run_status).toBe('succeeded');
  });

  it('a broken re-poll marks the slot recoverable instead of failing silently', async () => {
    seed();
    persistPendingGenTasks('p1', ['t1'], 'image');
    pollGeneration.mockRejectedValue(new Error('network down'));

    await resumePendingGenerations();

    expect(slotData()?.gen_recover).toEqual(['t1']);
    expect(promptData().gen_tasks).toEqual([]);
    expect(promptData().run_status).toBe('failed');
    expect(String(promptData().run_error)).toMatch(/not lost/i);
  });

  it('orphaned running prompts with no pending tasks reset to failed', async () => {
    seed();
    await resumePendingGenerations();
    expect(promptData().run_status).toBe('failed');
    expect(String(promptData().run_error)).toMatch(/reload/i);
  });

  it('concurrent resume for the same canvas is a no-op (StrictMode double-mount)', async () => {
    seed();
    persistPendingGenTasks('p1', ['t1'], 'image');
    let resolvePoll!: (v: unknown) => void;
    pollGeneration.mockImplementation(
      () => new Promise((res) => { resolvePoll = res; }),
    );

    const first = resumePendingGenerations();
    const second = resumePendingGenerations();
    resolvePoll({ phase: 'completed', metadata: { result_url: '/gm/1/cover' } });
    await Promise.all([first, second]);

    expect(pollGeneration).toHaveBeenCalledTimes(1);
    expect((slotData()?.images as unknown[]).length).toBe(1);
  });

  it('never writes into another canvas after a switch', async () => {
    seed();
    persistPendingGenTasks('p1', ['t1'], 'image');
    let resolvePoll!: (v: unknown) => void;
    pollGeneration.mockImplementation(
      () => new Promise((res) => { resolvePoll = res; }),
    );

    const done = resumePendingGenerations();
    useCanvasCoreStore.setState({ canvasId: 'other' });
    resolvePoll({ phase: 'completed', metadata: { result_url: '/gm/1/cover' } });
    await done;

    expect(slotData()).toBeNull();
  });
});

describe('requeryRecoverTask (P1-13)', () => {
  function seedRecoverSlot(): void {
    const slot: CanvasNode = {
      id: 'out1',
      type: 'output',
      position: { x: 320, y: 0 },
      data: {
        kind: 'image',
        images: [],
        gen_slot: { node_id: 'p1', index: 0 },
        gen_recover: ['t1'],
        gen_failed: 0,
      },
    } as unknown as CanvasNode;
    seed([slot]);
  }

  it('completed task lands its url and clears the recover mark', async () => {
    seedRecoverSlot();
    getGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/7/cover' },
    });
    const outcome = await requeryRecoverTask('p1', 't1');
    expect(outcome).toBe('completed');
    const slot = slotData();
    expect(slot?.gen_recover).toEqual([]);
    expect(slot?.images).toEqual([{ url: '/gm/7/cover', kind: 'image' }]);
  });

  it('terminal failure burns the recover mark into the failed count', async () => {
    seedRecoverSlot();
    getGeneration.mockResolvedValue({ phase: 'failed', error_msg: 'no credit' });
    const outcome = await requeryRecoverTask('p1', 't1');
    expect(outcome).toBe('failed');
    const slot = slotData();
    expect(slot?.gen_recover).toEqual([]);
    expect(slot?.gen_failed).toBe(1);
  });

  it('still-running task keeps the recover state (pending)', async () => {
    seedRecoverSlot();
    getGeneration.mockResolvedValue({ phase: 'in_progress' });
    const outcome = await requeryRecoverTask('p1', 't1');
    expect(outcome).toBe('pending');
    expect(slotData()?.gen_recover).toEqual(['t1']);
  });

  it('query errors report pending (task remains re-queryable)', async () => {
    seedRecoverSlot();
    getGeneration.mockRejectedValue(new Error('network down'));
    const outcome = await requeryRecoverTask('p1', 't1');
    expect(outcome).toBe('pending');
    expect(slotData()?.gen_recover).toEqual(['t1']);
  });
});
