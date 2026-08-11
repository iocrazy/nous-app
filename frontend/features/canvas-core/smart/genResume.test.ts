// features/canvas-core/smart/genResume.test.ts
// Broken-connection recovery (P1-13 — Infinite's resumeSmartPendingTasks):
// in-flight generation task ids persist on the prompt node (data.gen_tasks,
// survives refresh via nodes_json), a reload re-attaches polling, and a
// recover-marked task can be re-queried once from the slot overlay.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getGeneration = vi.fn();
const pollGeneration = vi.fn();
const cancelGeneration = vi.fn();
vi.mock('../services/canvasGenerationService', async () => {
  const actual = await vi.importActual<
    typeof import('../services/canvasGenerationService')
  >('../services/canvasGenerationService');
  return {
    ...actual,
    getGeneration: (...a: unknown[]) => getGeneration(...a),
    pollGeneration: (...a: unknown[]) => pollGeneration(...a),
    cancelGeneration: (...a: unknown[]) => cancelGeneration(...a),
  };
});

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  cancelPendingGenTasks,
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

describe('cancelPendingGenTasks (P1-1)', () => {
  it('cancels every in-flight task on the node server-side', async () => {
    seed();
    persistPendingGenTasks('p1', ['t1', 't2'], 'image');
    cancelGeneration.mockResolvedValue(undefined);

    await cancelPendingGenTasks('p1');

    expect(cancelGeneration).toHaveBeenCalledTimes(2);
    expect(cancelGeneration).toHaveBeenCalledWith('t1');
    expect(cancelGeneration).toHaveBeenCalledWith('t2');
  });

  it('is best-effort — a failing cancel logs and never rejects', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    seed();
    persistPendingGenTasks('p1', ['t1'], 'video');
    cancelGeneration.mockRejectedValue(new Error('boom'));

    await expect(cancelPendingGenTasks('p1')).resolves.toBeUndefined();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('no-ops when the node has no pending tasks', async () => {
    seed();
    await cancelPendingGenTasks('p1');
    expect(cancelGeneration).not.toHaveBeenCalled();
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

describe('resumePendingGenerations — stranded shot reconcile (Task 3 fix-round-1)', () => {
  function shotNode(data: Record<string, unknown>): CanvasNode {
    return {
      id: 'shot1',
      type: 'shot',
      position: { x: 0, y: 0 },
      data: {
        title: '',
        reference_resource_ids: [],
        notes: '',
        shot_id: '77',
        shot_label: '1-1',
        shot_type: null,
        camera_angle: null,
        camera_movement: null,
        focal_length: null,
        description: null,
        image_url: null,
        shot_status: null,
        scene_id: '1',
        ...data,
      },
    } as unknown as CanvasNode;
  }

  function shotData(): Record<string, unknown> {
    const n = useCanvasCoreStore.getState().nodes.find((x) => asObj(x).id === 'shot1');
    return (asObj(n).data ?? {}) as Record<string, unknown>;
  }

  it('drops a stranded generating shot (no image_url) to failed — button re-enables', async () => {
    seed([shotNode({ shot_status: 'generating', image_url: null })]);
    await resumePendingGenerations();
    expect(shotData().shot_status).toBe('failed');
  });

  it('does NOT lie about a generation that actually finished — image_url landed → done', async () => {
    seed([
      shotNode({ shot_status: 'generating', image_url: '/api/v1/generated-media/9/cover' }),
    ]);
    await resumePendingGenerations();
    expect(shotData().shot_status).toBe('done');
    expect(shotData().image_url).toBe('/api/v1/generated-media/9/cover');
  });

  it('leaves a shot node alone when it is not mid-generation', async () => {
    seed([shotNode({ shot_status: 'done', image_url: '/x' })]);
    await resumePendingGenerations();
    expect(shotData().shot_status).toBe('done');

    seed([shotNode({ shot_status: null, image_url: null })]);
    await resumePendingGenerations();
    expect(shotData().shot_status).toBeNull();
  });

  it('never touches unbound shot nodes (shot_status stays null there in practice)', async () => {
    seed([shotNode({ shot_id: null, shot_status: null })]);
    await resumePendingGenerations();
    expect(shotData().shot_status).toBeNull();
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
