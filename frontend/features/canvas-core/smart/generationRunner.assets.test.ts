// features/canvas-core/smart/generationRunner.assets.test.ts
//
// What an upstream `asset` card actually changes about the dispatched request
// (P4 Task 5): references lead `source_urls`, the bundle's positive text
// prefixes the body, and one merged `negative` ships for the backend to accept
// or drop.

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

import { noAssetInputs, withGenerationRunner } from './generationRunner';
import type { ComposedAssetInputs } from './promptInputs';
import type { RunnerContext, RunnerResult } from './runner';

const base = vi.fn(async (): Promise<RunnerResult> => ({ ok: true, text: '', error: null }));

afterEach(() => vi.clearAllMocks());

const GEN_CTX: RunnerContext = {
  promptId: 'p1',
  body: 'a cat on a roof',
  provider_slug: '',
  agent_id: null,
  gen: { kind: 'image', model: 'codex', count: 1 },
};

const composed = (over: Partial<ComposedAssetInputs> = {}): ComposedAssetInputs => ({
  reference_urls: [],
  prompt_prefix: '',
  negative: '',
  contributions: [],
  ...over,
});

function runner(assetInputs = noAssetInputs) {
  dispatchGenerations.mockResolvedValue(['t1']);
  pollGeneration.mockResolvedValue({
    phase: 'completed',
    metadata: { result_url: '/api/v1/generated-media/1/cover' },
  });
  return withGenerationRunner(base, { canvasId: '9', assetInputs });
}

const dispatched = () => dispatchGenerations.mock.calls[0][1];

describe('withGenerationRunner — asset composition', () => {
  it('asks the resolver with the prompt id and the run MODEL', async () => {
    // The bundle depends on the provider's reference ceiling, so the model is
    // not optional context — it is what makes the answer different.
    const spy = vi.fn(async () => composed());
    await runner(spy)({ ...GEN_CTX, gen: { kind: 'image', model: 'seedream-4', count: 1 } });
    expect(spy).toHaveBeenCalledWith('p1', 'seedream-4');
  });

  it('puts asset references AHEAD of the wired i2i inputs', async () => {
    const inputs = composed({ reference_urls: ['/api/v1/resources/10/cover'] });
    await runner(async () => inputs)({
      ...GEN_CTX,
      source_urls: ['/api/v1/generated-media/7/cover'],
    });
    expect(dispatched().params.source_urls).toEqual([
      '/api/v1/resources/10/cover',
      '/api/v1/generated-media/7/cover',
    ]);
  });

  it('dedupes a reference the graph also supplies', async () => {
    const inputs = composed({ reference_urls: ['/api/v1/resources/10/cover'] });
    await runner(async () => inputs)({
      ...GEN_CTX,
      source_urls: ['/api/v1/resources/10/cover', '/api/v1/generated-media/7/cover'],
    });
    expect(dispatched().params.source_urls).toEqual([
      '/api/v1/resources/10/cover',
      '/api/v1/generated-media/7/cover',
    ]);
  });

  it('carries asset references on a VIDEO run too', async () => {
    const inputs = composed({ reference_urls: ['/api/v1/resources/10/cover'] });
    await runner(async () => inputs)({
      ...GEN_CTX,
      gen: { kind: 'video', model: 'jimeng', video_mode: 'multimodal' },
    });
    expect(dispatched().params.source_urls).toEqual(['/api/v1/resources/10/cover']);
  });

  it('prefixes the prompt body with the bundle text', async () => {
    const inputs = composed({ prompt_prefix: 'Ava, red coat' });
    await runner(async () => inputs)(GEN_CTX);
    expect(dispatched().prompt).toBe('Ava, red coat\na cat on a roof');
  });

  it('prefixes EVERY split item, not just the first', async () => {
    const inputs = composed({ prompt_prefix: 'Ava' });
    await runner(async () => inputs)({
      ...GEN_CTX,
      split_prompts: ['on a roof', 'in a car'],
    });
    expect(dispatchGenerations.mock.calls.map((c) => c[1].prompt)).toEqual([
      'Ava\non a roof',
      'Ava\nin a car',
    ]);
  });

  it('leaves the body alone when no asset contributes text', async () => {
    await runner()(GEN_CTX);
    expect(dispatched().prompt).toBe('a cat on a roof');
  });

  it('merges the node negative and the asset negative into one params.negative', async () => {
    const inputs = composed({ negative: 'blurry' });
    await runner(async () => inputs)({ ...GEN_CTX, negative_body: 'extra fingers' });
    expect(dispatched().params.negative).toBe('extra fingers\nblurry');
  });

  it('dedupes identical negatives', async () => {
    const inputs = composed({ negative: 'blurry' });
    await runner(async () => inputs)({ ...GEN_CTX, negative_body: 'blurry' });
    expect(dispatched().params.negative).toBe('blurry');
  });

  it('sends no negative key at all when there is none', async () => {
    await runner()(GEN_CTX);
    expect('negative' in dispatched().params).toBe(false);
  });

  it('ships the negative even for a provider that cannot take one', async () => {
    // Deliberate: `GenerationRequest.reconcile` drops it and names `negative`
    // in `dropped_knobs`, which the node already renders. Second-guessing the
    // provider here would put a second, quieter answer in front of the one the
    // backend reports — and the quiet one leaves nothing on screen.
    const inputs = composed({ negative: 'blurry' });
    await runner(async () => inputs)({
      ...GEN_CTX,
      gen: { kind: 'image', model: 'ark-seedream', count: 1 },
    });
    expect(dispatched().params.negative).toBe('blurry');
  });

});
