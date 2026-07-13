// features/canvas-core/smart/generationRunner.test.ts
// Generation-aware PromptCaller (G4-F1): text prompts pass through to the
// base caller; image/video prompts dispatch count× backend tasks and poll
// them all, returning the durable result URLs.

import { afterEach, describe, expect, it, vi } from 'vitest';

const dispatchGenerations = vi.fn();
const pollGeneration = vi.fn();
vi.mock('../services/canvasGenerationService', async () => {
  const actual = await vi.importActual<
    typeof import('../services/canvasGenerationService')
  >('../services/canvasGenerationService');
  return {
    PollStopped: actual.PollStopped,
    dispatchGenerations: (...a: unknown[]) => dispatchGenerations(...a),
    pollGeneration: (...a: unknown[]) => pollGeneration(...a),
  };
});

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

  it('stamps entity ownership into dispatch params (CC5 asset backlink)', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/api/v1/generated-media/1/cover', media_kind: 'image' },
    });

    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', ratio: '3:4', count: 1 },
      entity_ref: { kind: 'character', id: '123456789' },
    });

    expect(dispatchGenerations).toHaveBeenCalledWith('9', {
      node_id: 'p1',
      kind: 'image',
      prompt: 'hello',
      model: '',
      count: 1,
      params: { ratio: '3:4', entity_kind: 'character', entity_id: '123456789' },
    });
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

describe('withGenerationRunner — G4-F3 additions', () => {
  const GEN_CTX: RunnerContext = {
    promptId: 'p1',
    body: 'a cat',
    provider_slug: '',
    agent_id: null,
    gen: { kind: 'image', model: 'm', count: 1 },
  };

  it('forwards ctx.source_url to the dispatch (i2i / i2v input)', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/cover' },
    });
    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    await runner({ ...GEN_CTX, source_url: '/api/v1/generated-media/7/cover' });
    expect(dispatchGenerations).toHaveBeenCalledWith(
      '9',
      expect.objectContaining({ source_url: '/api/v1/generated-media/7/cover' }),
    );
  });

  it('reports queued → running through onPhase while polling', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockImplementation(async (_id: string, opts: { onTick?: (t: unknown) => void }) => {
      opts.onTick?.({ phase: 'queued' });
      opts.onTick?.({ phase: 'in_progress' });
      return { phase: 'completed', metadata: { result_url: '/gm/1/cover' } };
    });
    const phases: string[] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onPhase: (id, phase) => phases.push(`${id}:${phase}`),
    });
    await runner(GEN_CTX);
    expect(phases).toEqual(['p1:queued', 'p1:running']);
  });
});

describe('withGenerationRunner — fan-out partial failure (P0-2)', () => {
  it('keeps the successful urls when only some tasks fail', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2', 't3']);
    pollGeneration
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/api/v1/generated-media/1/cover' },
      })
      .mockResolvedValueOnce({ phase: 'failed', error_msg: 'no credit' })
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/api/v1/generated-media/3/cover' },
      });

    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 3 },
    });

    // Infinite semantics: good items land, bad ones are reported — never
    // throw away completed results because a sibling task failed.
    expect(result.ok).toBe(true);
    expect(result.urls).toEqual([
      '/api/v1/generated-media/1/cover',
      '/api/v1/generated-media/3/cover',
    ]);
    expect(result.error).toContain('1 of 3');
    expect(result.error).toContain('no credit');
  });

  it('still fails the prompt when every task fails', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    pollGeneration
      .mockResolvedValueOnce({ phase: 'failed', error_msg: 'no credit' })
      .mockResolvedValueOnce({ phase: 'timeout' });

    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 2 },
    });
    expect(result.ok).toBe(false);
    expect(result.error).toContain('no credit');
  });

  it('keeps error null when everything succeeds', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValueOnce({
      phase: 'completed',
      metadata: { result_url: '/u1' },
    });
    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 1 },
    });
    expect(result.ok).toBe(true);
    expect(result.error).toBeNull();
  });
});

describe('withGenerationRunner — recover semantics (P1-13)', () => {
  const GEN2: RunnerContext = {
    ...TEXT_CTX,
    gen: { kind: 'image', model: '', count: 2 },
  };

  it('onDispatched carries the task ids (persistence hook for resume)', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/u' },
    });
    const dispatched: unknown[] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDispatched: (...a) => dispatched.push(a),
    });
    await runner(GEN2);
    expect(dispatched).toEqual([['p1', 2, 'image', ['t1', 't2']]]);
  });

  it('onItemSettled carries each task id', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/u' },
    });
    const settled: Array<{ taskId?: string }> = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onItemSettled: (_id, item) => settled.push(item),
    });
    await runner({ ...TEXT_CTX, gen: { kind: 'image', model: '', count: 1 } });
    expect(settled[0].taskId).toBe('t1');
  });

  it('a poll exception marks THAT item recoverable and keeps siblings', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    pollGeneration
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/2/cover' },
      });
    const settled: Array<{ taskId?: string; url: string | null; recoverable?: boolean }> = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onItemSettled: (_id, item) => settled.push(item),
    });
    const result = await runner(GEN2);
    // The broken poll must NOT reject the whole batch (latent Promise.all
    // bug): the sibling's completed url still lands.
    expect(result.ok).toBe(true);
    expect(result.urls).toEqual(['/gm/2/cover']);
    const recover = settled.find((s) => s.taskId === 't1');
    expect(recover?.recoverable).toBe(true);
    expect(recover?.url).toBeNull();
  });

  it('all polls broken → in-band failure that says the tasks are not lost', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockRejectedValue(new Error('network down'));
    const runner = withGenerationRunner(baseCaller, { canvasId: '9' });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 1 },
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/not lost/i);
  });

  it('PollStopped still stops the whole run (no recover state)', async () => {
    const { PollStopped } = await import('../services/canvasGenerationService');
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockRejectedValue(new PollStopped('t1'));
    const settled: Array<{ recoverable?: boolean }> = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onItemSettled: (_id, item) => settled.push(item),
    });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 1 },
    });
    expect(result.stopped).toBe(true);
    expect(settled.some((s) => s.recoverable)).toBe(false);
  });
});

describe('withGenerationRunner — placeholder lifecycle (P0-3)', () => {
  it('fires onDispatched with the fan-out size and onItemSettled per item', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    let resolveSlow!: (v: unknown) => void;
    pollGeneration
      .mockImplementationOnce(
        () => new Promise((res) => { resolveSlow = res; }),
      )
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/2/cover' },
      });

    const dispatched: Array<[string, number, string]> = [];
    const settled: Array<{ url: string | null }> = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDispatched: (id, count, kind) => dispatched.push([id, count, kind]),
      onItemSettled: (_id, item) => settled.push(item),
    });
    const done = runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: '', count: 2 },
    });

    // The FAST task settles before the slow sibling resolves: first-done-
    // first-shown, no batch barrier.
    await vi.waitFor(() => expect(settled).toHaveLength(1));
    expect(settled[0].url).toBe('/gm/2/cover');
    expect(dispatched).toEqual([['p1', 2, 'image']]);

    resolveSlow({ phase: 'failed', error_msg: 'boom' });
    const result = await done;
    expect(settled).toHaveLength(2);
    expect(settled[1].url).toBeNull();
    expect(result.ok).toBe(true); // P0-2: the good item still lands
  });
});
