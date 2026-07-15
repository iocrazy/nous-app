import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/canvasGenerationService', () => ({
  dispatchGenerations: vi.fn(async () => ['task-1']),
  pollGeneration: vi.fn(async () => ({
    phase: 'completed',
    metadata: { result_url: '/api/v1/generated-media/out.png' },
  })),
  PollStopped: class PollStopped extends Error {},
}));

import { dispatchGenerations } from '../services/canvasGenerationService';
import { __test_resolveCaller } from './loopRun';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

afterEach(() => vi.clearAllMocks());

describe('loop caller drives image generation', () => {
  it('routes a gen prompt to dispatchGenerations and returns its urls', async () => {
    useCanvasCoreStore.setState({ canvasId: 'cv-1' } as never);
    const caller = __test_resolveCaller('loop-1');
    const result = await caller({
      promptId: 'p-1',
      body: 'a cat',
      provider_slug: '',
      agent_id: null,
      gen: { kind: 'image', model: 'm', count: 1 },
      source_url: '/api/v1/generated-media/ref.png',
    });
    expect(dispatchGenerations).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(true);
    expect(result.urls).toEqual(['/api/v1/generated-media/out.png']);
  });
});
