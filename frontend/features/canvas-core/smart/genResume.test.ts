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

import { ApiError } from '../../../services/apiClient';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  __clearActiveShotPolls,
  cancelPendingGenTasks,
  clearPendingGenTasks,
  persistPendingGenTasks,
  prunePendingGenTask,
  registerActiveShotPoll,
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
  // fix-round-3: activeShotPolls is module-level state (survives remount by
  // design) — must be reset between tests or a leftover claim blocks the
  // next test's re-attach.
  __clearActiveShotPolls();
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

describe('resumePendingGenerations — stranded shot reconcile (Task 3, fix-round-1 + round-2)', () => {
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
        gen_task_id: null,
        scene_id: '1',
        ...data,
      },
    } as unknown as CanvasNode;
  }

  function shotData(): Record<string, unknown> {
    const n = useCanvasCoreStore.getState().nodes.find((x) => asObj(x).id === 'shot1');
    return (asObj(n).data ?? {}) as Record<string, unknown>;
  }

  // ---- No task id persisted (fix-round-1 branch — dispatch itself never
  // got far enough to persist gen_task_id): local-mirror-only reconcile. ----

  it('no task id + no image_url → truly stranded, drops to failed (button re-enables)', async () => {
    seed([shotNode({ shot_status: 'generating', gen_task_id: null, image_url: null })]);
    await resumePendingGenerations();
    expect(shotData().shot_status).toBe('failed');
    expect(pollGeneration).not.toHaveBeenCalled();
  });

  it('no task id but image_url already landed → does NOT lie about a finished result', async () => {
    seed([
      shotNode({
        shot_status: 'generating',
        gen_task_id: null,
        image_url: '/api/v1/generated-media/9/cover',
      }),
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

  // ---- Task id persisted (fix-round-2 — re-attach by id, prompt gen_tasks
  // parity): this is the common case, since ShotNodeView persists
  // gen_task_id the instant dispatch returns. ----

  describe('re-attach by gen_task_id (fix-round-2)', () => {
    it('in-flight re-entry: does NOT reset to failed while the re-attached poll is pending', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-9', image_url: null })]);
      let resolvePoll!: (v: unknown) => void;
      pollGeneration.mockImplementation(
        () => new Promise((res) => { resolvePoll = res; }),
      );

      const done = resumePendingGenerations();
      // Yield a microtask so the reconcile loop has started the re-attach.
      await Promise.resolve();
      expect(shotData().shot_status).toBe('generating');
      expect(pollGeneration).toHaveBeenCalledWith(
        'task-9',
        expect.objectContaining({}),
      );

      resolvePoll({ phase: 'completed', metadata: { result_url: '/gm/1/cover' } });
      await done;

      expect(shotData().shot_status).toBe('done');
      expect(shotData().image_url).toBe('/gm/1/cover');
      expect(shotData().gen_task_id).toBeNull();
    });

    it('re-attached poll settling failed clears the task id and drops to failed', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-9', image_url: null })]);
      pollGeneration.mockResolvedValue({ phase: 'failed', metadata: {} });

      await resumePendingGenerations();

      expect(shotData().shot_status).toBe('failed');
      expect(shotData().gen_task_id).toBeNull();
    });

    it('a canvas switch mid-re-attach drops the patch (sameCanvas guard)', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-9', image_url: null })]);
      let resolvePoll!: (v: unknown) => void;
      pollGeneration.mockImplementation(
        () => new Promise((res) => { resolvePoll = res; }),
      );

      const done = resumePendingGenerations();
      useCanvasCoreStore.setState({ canvasId: 'other-canvas' });
      resolvePoll({ phase: 'completed', metadata: { result_url: '/gm/1/cover' } });
      await done;

      // The node under id 'shot1' in the (old) canvasId='9' document must
      // not have been mutated by a poll that settled after the switch.
      expect(shotData().shot_status).toBe('generating');
      expect(shotData().gen_task_id).toBe('task-9');
      expect(shotData().image_url).toBeNull();
    });
  });

  describe('404 unknown-terminal handling (final review Important 2)', () => {
    // task_tracking rows are pruned 7 days after reaching a terminal phase
    // (scheduled_cleanup.py) — a `gen_task_id` that survived that long 404s
    // on every re-poll forever. This must NOT fall into the "poll chain
    // broke, leave state alone" branch (that would retry-loop on every
    // mount and permanently block shotSync's in-flight guard from ever
    // refreshing image_url/shot_status from the script_shots mirror again).

    it('404 with no local image_url yet: clears the task id and drops to failed', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-gone', image_url: null })]);
      pollGeneration.mockRejectedValue(new ApiError('not found', 404));

      await resumePendingGenerations();

      expect(shotData().shot_status).toBe('failed');
      expect(shotData().gen_task_id).toBeNull();
    });

    it('404 but a frame already landed locally: resolves to done instead of lying with failed', async () => {
      seed([
        shotNode({
          shot_status: 'generating',
          gen_task_id: 'task-gone',
          image_url: '/gm/backfilled/cover',
        }),
      ]);
      pollGeneration.mockRejectedValue(new ApiError('not found', 404));

      await resumePendingGenerations();

      expect(shotData().shot_status).toBe('done');
      expect(shotData().gen_task_id).toBeNull();
      expect(shotData().image_url).toBe('/gm/backfilled/cover');
    });

    it('a later reconcile pass can pick up a fresh mirror refresh once the 404 branch cleared the in-flight guard', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-gone', image_url: null })]);
      pollGeneration.mockRejectedValue(new ApiError('not found', 404));
      await resumePendingGenerations();
      expect(shotData().shot_status).toBe('failed');
      expect(shotData().gen_task_id).toBeNull();

      // Simulate reconcileShotNodes's own settled-node mirror patch (Task 4)
      // now being free to land, since isInFlight() no longer reads
      // shot_status==='generating' && gen_task_id set for this node.
      useCanvasCoreStore
        .getState()
        .patchNode('shot1', { data: { shot_status: 'done', image_url: '/gm/real/cover' } });
      expect(shotData().shot_status).toBe('done');
      expect(shotData().image_url).toBe('/gm/real/cover');
    });

    it('a non-404 ApiError (e.g. 500) still follows the transient-error "leave alone" branch', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-500', image_url: null })]);
      pollGeneration.mockRejectedValue(new ApiError('server error', 500));

      await resumePendingGenerations();

      expect(shotData().shot_status).toBe('generating');
      expect(shotData().gen_task_id).toBe('task-500');
    });
  });

  describe('active shot poll registry (fix-round-3 — prevents dual pollers)', () => {
    it('reconcile does NOT attach a second poller when the task is already claimed (original handleGenerate closure still owns it)', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-live', image_url: null })]);
      // Simulate ShotNodeView.handleGenerate's own dispatch closure — still
      // running (unmount never cancelled it) — already owning this task id.
      registerActiveShotPoll('9', 'task-live');

      await resumePendingGenerations();

      expect(pollGeneration).not.toHaveBeenCalled();
      // Untouched — the original closure, not this reconcile pass, is
      // responsible for landing whatever terminal state this task reaches.
      expect(shotData().shot_status).toBe('generating');
      expect(shotData().gen_task_id).toBe('task-live');
    });

    it('a transient poll-chain error leaves shot_status/gen_task_id alone so a LATER reconcile can retry', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-flaky', image_url: null })]);
      pollGeneration.mockRejectedValueOnce(new Error('network hiccup'));

      await resumePendingGenerations();

      // NOT dropped to failed, NOT cleared — only the poll ATTEMPT broke;
      // the task's own server-side fate is still unknown.
      expect(shotData().shot_status).toBe('generating');
      expect(shotData().gen_task_id).toBe('task-flaky');

      // The registry claim was released in `finally` despite the throw, so
      // a later reconcile pass (another nav back into this canvas) can
      // retry re-attaching instead of being blocked forever.
      pollGeneration.mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/2/cover' },
      });
      await resumePendingGenerations();

      expect(pollGeneration).toHaveBeenCalledTimes(2);
      expect(shotData().shot_status).toBe('done');
      expect(shotData().image_url).toBe('/gm/2/cover');
      expect(shotData().gen_task_id).toBeNull();
    });

    it('a server-confirmed terminal failure IS authoritative — failed + task id cleared (not the transient-error path)', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-dead', image_url: null })]);
      // pollGeneration RESOLVES (not throws) with a terminal failure phase —
      // this is the server's own verdict, distinct from a broken poll chain.
      pollGeneration.mockResolvedValue({ phase: 'failed', metadata: {} });

      await resumePendingGenerations();

      expect(shotData().shot_status).toBe('failed');
      expect(shotData().gen_task_id).toBeNull();
    });

    it('the registry claim releases after a terminal state lands, never leaking a stale block on the task id', async () => {
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-once', image_url: null })]);
      pollGeneration.mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/3/cover' },
      });
      await resumePendingGenerations();
      expect(shotData().shot_status).toBe('done');

      // Re-seed the SAME task id onto a fresh generating shot purely to
      // exercise the release mechanic directly (a real generate cycle
      // always mints a new task id; this proves `finally` actually ran
      // rather than relying on task-id uniqueness to hide a leak).
      seed([shotNode({ shot_status: 'generating', gen_task_id: 'task-once', image_url: null })]);
      pollGeneration.mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/4/cover' },
      });
      await resumePendingGenerations();

      expect(pollGeneration).toHaveBeenCalledTimes(2);
      expect(shotData().image_url).toBe('/gm/4/cover');
    });
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
