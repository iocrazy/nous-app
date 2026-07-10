// features/canvas-core/smart/generationRunner.test.ts
// Generation-aware PromptCaller (G4-F1): text prompts pass through to the
// base caller; image/video prompts dispatch count× backend tasks and poll
// them all, returning the durable result URLs.

import { afterEach, describe, expect, it, vi } from 'vitest';

const dispatchGenerations = vi.fn();
const pollGeneration = vi.fn();
vi.mock('../services/canvasGenerationService', () => ({
  dispatchGenerations: (...a: unknown[]) => dispatchGenerations(...a),
  pollGeneration: (...a: unknown[]) => pollGeneration(...a),
}));

import { withGenerationRunner } from './generationRunner';
import type { RunnerContext, RunnerResult } from './runner';

const baseCaller = vi.fn(
  async (): Promise<RunnerResult> => ({ ok: true, text: 'llm', error: null }),
);

afterEach(() => vi.clearAllMocks());

const TEXT_CTX: RunnerContext = {
  promptId: 'p1',
  body: 'hello',
  provider_slug: '',
  agent_id: null,
};

describe('withGenerationRunner', () => {
  it('passes text prompts through to the base caller', async () => {
    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner(TEXT_CTX);
    expect(baseCaller).toHaveBeenCalled();
    expect(result.text).toBe('llm');
    expect(dispatchGenerations).not.toHaveBeenCalled();
  });

  it('dispatches image generation and returns the result urls', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    pollGeneration
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/api/v1/generated-media/1/cover', media_kind: 'image' },
      })
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/api/v1/generated-media/2/cover', media_kind: 'image' },
      });

    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: 'jimeng-cli-image', ratio: '16:9', count: 2 },
    });

    expect(dispatchGenerations).toHaveBeenCalledWith('9', {
      node_id: 'p1',
      kind: 'image',
      prompt: 'hello',
      model: 'jimeng-cli-image',
      count: 2,
      params: { ratio: '16:9' },
    });
    expect(result.ok).toBe(true);
    expect(result.urls).toEqual([
      '/api/v1/generated-media/1/cover',
      '/api/v1/generated-media/2/cover',
    ]);
    expect(result.media_kind).toBe('image');
    expect(baseCaller).not.toHaveBeenCalled();
  });

  it('reports a failed task in-band', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({ phase: 'failed', error_msg: 'no credit' });

    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 1 },
    });

    expect(result.ok).toBe(false);
    expect(result.error).toContain('no credit');
  });

  it('reports dispatch errors in-band instead of throwing', async () => {
    dispatchGenerations.mockRejectedValue(new Error('HTTP 500'));
    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'video', model: '', aspect: '16:9' },
    });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('HTTP 500');
  });

  it('falls back to the base caller when no canvas is loaded', async () => {
    const runner = withGenerationRunner(baseCaller, { canvasId: null });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 1 },
    });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('canvas');
  });
});
