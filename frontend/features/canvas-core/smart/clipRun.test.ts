// features/canvas-core/smart/clipRun.test.ts
// M1 MiniMax workbench: per-clip generation — dispatches ONE video task for
// one segment (its own prompt + optional per-clip reference), polls, and
// lands result_url on that segment.

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/canvasGenerationService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/canvasGenerationService')>();
  return {
    ...actual,
    dispatchGenerations: vi.fn(async () => ['task-1']),
    pollGeneration: vi.fn(async () => ({
      phase: 'completed',
      metadata: { result_url: '/api/v1/generated-media/9/stream' },
    })),
  };
});

import {
  dispatchGenerations,
  pollGeneration,
} from '../services/canvasGenerationService';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';
import { runSegmentClip } from './clipRun';

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '7',
    nodes: [
      {
        id: 't1',
        type: 'timeline',
        position: { x: 0, y: 0 },
        data: {
          model: 'jimeng-cli-seedance',
          aspect: '16:9',
          segments: [
            { id: 'a', prompt: 'sunrise pan', seconds: 4, ref_url: '/api/v1/generated-media/1/cover' },
            { id: 'b', prompt: '', seconds: 3 },
          ],
        },
      } as unknown as CanvasNode,
    ],
    connections: [],
    selection: [],
  });
}

function segs() {
  return (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 't1') as {
    data: { segments: Array<{ id: string; result_url?: string }> };
  }).data.segments;
}

afterEach(() => {
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('runSegmentClip', () => {
  it('dispatches one video task with the clip prompt + reference and lands the result', async () => {
    seed();
    const out = await runSegmentClip('t1', 'a');
    expect(out.ok).toBe(true);
    expect(dispatchGenerations).toHaveBeenCalledWith('7', {
      node_id: 't1',
      kind: 'video',
      prompt: 'sunrise pan',
      model: 'jimeng-cli-seedance',
      count: 1,
      params: { aspect: '16:9' },
      source_url: '/api/v1/generated-media/1/cover',
    });
    expect(pollGeneration).toHaveBeenCalledWith('task-1', expect.anything());
    expect(segs()[0].result_url).toBe('/api/v1/generated-media/9/stream');
  });

  it('empty prompt → typed refusal without dispatching', async () => {
    seed();
    const out = await runSegmentClip('t1', 'b');
    expect(out.ok).toBe(false);
    expect(out.error).toMatch(/prompt/i);
    expect(dispatchGenerations).not.toHaveBeenCalled();
  });

  it('failed task → ok:false with the error surfaced', async () => {
    seed();
    (pollGeneration as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      phase: 'failed',
      error_msg: 'no video model configured',
    });
    const out = await runSegmentClip('t1', 'a');
    expect(out.ok).toBe(false);
    expect(out.error).toMatch(/no video model/);
    expect(segs()[0].result_url).toBeUndefined();
  });
});
