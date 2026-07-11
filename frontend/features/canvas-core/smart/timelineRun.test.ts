// features/canvas-core/smart/timelineRun.test.ts
// Timeline run wiring (G8-F1): dispatch one film task, poll it, land the
// finished /stream URL in a video output slot. loopRun-style guards:
// canvasId snapshot, per-node re-entrancy, failure marks the node.

import { afterEach, describe, expect, it, vi } from 'vitest';

const dispatchTimelineRun = vi.fn();
const pollGeneration = vi.fn();
vi.mock('../services/canvasGenerationService', () => ({
  dispatchTimelineRun: (...a: unknown[]) => dispatchTimelineRun(...a),
  pollGeneration: (...a: unknown[]) => pollGeneration(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { startTimelineRun, useTimelineRunStore } from './timelineRun';
import type { CanvasNode } from '../types';

const TL: CanvasNode = {
  id: 'tl1',
  type: 'timeline',
  position: { x: 0, y: 0 },
  data: {
    segments: [
      { id: 's1', prompt: 'opening', seconds: 5 },
      { id: 's2', prompt: 'waves', seconds: 3 },
    ],
    model: '',
    aspect: '16:9',
    run_status: 'idle',
  },
} as unknown as CanvasNode;

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    nodes: [TL],
    connections: [],
    selection: [],
  });
}

afterEach(() => {
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
  useTimelineRunStore.getState().reset();
});

describe('startTimelineRun', () => {
  it('dispatches, polls, and lands the film in a video output slot', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/api/v1/generated-media/9/stream' },
    });

    const ok = await startTimelineRun('tl1');
    expect(ok).toBe(true);

    expect(dispatchTimelineRun).toHaveBeenCalledWith('9', {
      node_id: 'tl1',
      segments: [
        { prompt: 'opening', seconds: 5 },
        { prompt: 'waves', seconds: 3 },
      ],
      model: '',
      aspect: '16:9',
    });

    const s = useCanvasCoreStore.getState();
    const slot = s.nodes.find(
      (n) => ((n as CanvasNode).data as { timeline_slot?: { node_id: string } })
        ?.timeline_slot?.node_id === 'tl1',
    ) as Record<string, unknown> | undefined;
    expect(slot).toBeTruthy();
    const data = slot!.data as { kind: string; preview_url: string };
    expect(data.kind).toBe('video');
    expect(data.preview_url).toBe('/api/v1/generated-media/9/stream');
    const tl = s.nodes.find((n) => (n as CanvasNode).id === 'tl1') as Record<string, unknown>;
    expect((tl.data as { run_status: string }).run_status).toBe('succeeded');
  });

  it('reuses the slot on re-runs instead of stacking new nodes', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/stream' },
    });
    await startTimelineRun('tl1');
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/2/stream' },
    });
    await startTimelineRun('tl1');

    const slots = useCanvasCoreStore.getState().nodes.filter(
      (n) => ((n as CanvasNode).data as { timeline_slot?: unknown })?.timeline_slot,
    );
    expect(slots).toHaveLength(1);
    expect(((slots[0] as CanvasNode).data as { preview_url: string }).preview_url).toBe(
      '/gm/2/stream',
    );
  });

  it('marks the node failed on a failed task and returns false', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    pollGeneration.mockResolvedValue({ phase: 'failed', error_msg: 'provider down' });

    const ok = await startTimelineRun('tl1');
    expect(ok).toBe(false);
    const tl = useCanvasCoreStore.getState().nodes[0] as Record<string, unknown>;
    expect((tl.data as { run_status: string; run_error: string }).run_status).toBe('failed');
    expect((tl.data as { run_error: string }).run_error).toBe('provider down');
  });

  it('refuses to write into another canvas after a mid-run switch', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    pollGeneration.mockImplementation(async () => {
      useCanvasCoreStore.setState({ canvasId: 'other' });
      return { phase: 'completed', metadata: { result_url: '/gm/1/stream' } };
    });
    const ok = await startTimelineRun('tl1');
    expect(ok).toBe(false);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('is re-entrancy safe per node', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    let release: (() => void) | null = null;
    pollGeneration.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({ phase: 'completed', metadata: { result_url: '/gm/1/stream' } });
        }),
    );
    const first = startTimelineRun('tl1');
    expect(await startTimelineRun('tl1')).toBe(false);
    release?.();
    await first;
    expect(dispatchTimelineRun).toHaveBeenCalledTimes(1);
  });
});

// ── Minimal-set upgrades (P2-1): live progress / thumbs / failed mark ──────

describe('startTimelineRun — progress decoration (P2-1)', () => {
  const tlData = () => {
    const node = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as Record<string, unknown>).id === 'tl1');
    return ((node as Record<string, unknown>).data ?? {}) as Record<string, unknown>;
  };

  it('onTick metadata drives run_progress and segment thumbnails', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    pollGeneration.mockImplementation(
      async (_id: string, opts: { onTick?: (t: unknown) => void }) => {
        opts.onTick?.({
          phase: 'in_progress',
          metadata: { segments_done: 1, segments_total: 2, segment_frames: ['/gm/f1/cover'] },
        });
        return {
          phase: 'completed',
          metadata: {
            result_url: '/api/v1/generated-media/9/stream',
            segment_frames: ['/gm/f1/cover'],
          },
        };
      },
    );

    const ok = await startTimelineRun('tl1');
    expect(ok).toBe(true);
    expect(tlData().segment_thumbs).toEqual(['/gm/f1/cover']);
    expect(tlData().run_progress).toBeNull();
    expect(tlData().failed_index).toBeNull();
  });

  it('a failed run marks the segment that broke (done count = failing index)', async () => {
    seed();
    dispatchTimelineRun.mockResolvedValue('task-1');
    pollGeneration.mockResolvedValue({
      phase: 'failed',
      error_msg: 'segment 2 provider error',
      metadata: { segments_done: 1, segments_total: 2 },
    });

    const ok = await startTimelineRun('tl1');
    expect(ok).toBe(false);
    expect(tlData().run_status).toBe('failed');
    expect(tlData().failed_index).toBe(1);
  });

  it('a fresh run clears the previous failed mark and progress', async () => {
    seed();
    useCanvasCoreStore.getState().patchNode('tl1', {
      data: { failed_index: 1, run_progress: { done: 1, total: 2 } },
    });
    dispatchTimelineRun.mockResolvedValue('task-2');
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/api/v1/generated-media/9/stream' },
    });

    await startTimelineRun('tl1');
    expect(tlData().failed_index).toBeNull();
    expect(tlData().run_progress).toBeNull();
  });
});
