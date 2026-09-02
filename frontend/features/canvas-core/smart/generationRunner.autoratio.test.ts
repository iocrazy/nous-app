// Resolving `ratio: 'auto'` at dispatch.
//
// The value means "match the image feeding this prompt". It has to be
// resolved when the run starts, not when the node was created: the wired
// input can change afterwards, and a ratio frozen at creation would quietly
// stop matching what the user is looking at.
//
// An explicit choice always wins. That is the half of the request that is
// easy to break — "default to the source" must not become "override me".

import { beforeEach, describe, expect, it, vi } from 'vitest';

const measureRatio = vi.fn<(url: string) => Promise<string | null>>();
vi.mock('./autoRatio', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./autoRatio')>();
  return { ...actual, measureRatio: (url: string) => measureRatio(url) };
});

const dispatchGenerations =
  vi.fn<(canvasId: string, req: { params?: Record<string, unknown> }) => Promise<string[]>>(
    async () => ['t1'],
  );
vi.mock('../services/canvasGenerationService', () => ({
  dispatchGenerations: (canvasId: string, req: { params?: Record<string, unknown> }) =>
    dispatchGenerations(canvasId, req),
  pollGeneration: vi.fn(async () => ({ ok: true, url: null, kind: 'image' })),
  PollStopped: class PollStopped extends Error {},
}));

import { withGenerationRunner } from './generationRunner';
import type { RunnerContext } from './runner';

const baseCtx = (over: Partial<RunnerContext> = {}): RunnerContext =>
  ({
    promptId: 'p1',
    body: 'a cat',
    provider_slug: null,
    agent_id: null,
    gen: { kind: 'image', model: 'm', ratio: 'auto', count: 1 },
    source_url: '/api/v1/generated-media/7/cover',
    source_urls: ['/api/v1/generated-media/7/cover'],
    entity_ref: null,
    ...over,
  }) as RunnerContext;

const run = (ctx: RunnerContext) =>
  withGenerationRunner(async () => ({ ok: true, text: '', error: null }), {
    canvasId: 'c1',
  })(ctx);

const dispatchedParams = () => dispatchGenerations.mock.calls[0]?.[1]?.params ?? {};

beforeEach(() => {
  dispatchGenerations.mockClear();
  measureRatio.mockReset();
});

describe('auto ratio at dispatch', () => {
  it('sends the measured ratio of the source image', async () => {
    measureRatio.mockResolvedValue('16:9');
    await run(baseCtx());
    expect(dispatchedParams().ratio).toBe('16:9');
  });

  it('measures the image the run actually uses as its source', async () => {
    measureRatio.mockResolvedValue('3:4');
    await run(baseCtx({ source_url: '/api/v1/generated-media/99/cover' }));
    expect(measureRatio).toHaveBeenCalledWith('/api/v1/generated-media/99/cover');
  });

  it('never overrides a ratio the user picked', async () => {
    measureRatio.mockResolvedValue('16:9');
    await run(baseCtx({ gen: { kind: 'image', model: 'm', ratio: '1:1', count: 1 } } as never));
    expect(dispatchedParams().ratio, 'an explicit 1:1 was replaced').toBe('1:1');
    expect(measureRatio, 'measured despite an explicit choice').not.toHaveBeenCalled();
  });

  it('omits ratio entirely when there is no source to follow', async () => {
    await run(baseCtx({ source_url: undefined, source_urls: [] } as never));
    expect(dispatchedParams()).not.toHaveProperty('ratio');
    expect(measureRatio).not.toHaveBeenCalled();
  });

  it('omits ratio when the source cannot be measured, rather than guessing', async () => {
    measureRatio.mockResolvedValue(null);
    await run(baseCtx());
    expect(dispatchedParams()).not.toHaveProperty('ratio');
  });
});

// ── The resolved ratio has to reach the output slot (Task 7 fix round 1) ────
// The slot reserves each cell's box from the ratio it was told about. If it
// is told the prompt's raw value it reserves a SQUARE for the default image
// run — `'auto'` is what the picker shows, and an unset value counts as auto
// too — while the generation itself comes back at the source's shape. Zero
// jump would then be achieved by reserving the wrong space. So the value the
// dispatch actually sent is what `onDispatched` must carry.

const dispatchedRatioArg = async (ctx: RunnerContext): Promise<unknown> => {
  const seen: unknown[][] = [];
  await withGenerationRunner(async () => ({ ok: true, text: '', error: null }), {
    canvasId: 'c1',
    onDispatched: (...a: unknown[]) => seen.push(a),
  })(ctx);
  return seen[0]?.[4];
};

describe('onDispatched carries the ratio the run was dispatched with', () => {
  it('reports the MEASURED ratio when the prompt says auto', async () => {
    measureRatio.mockResolvedValue('16:9');
    expect(await dispatchedRatioArg(baseCtx())).toBe('16:9');
  });

  it('reports the measured ratio when the prompt leaves it unset', async () => {
    // Unset is auto (autoRatio.isAutoRatio) — the same default path.
    measureRatio.mockResolvedValue('3:4');
    const ctx = baseCtx({ gen: { kind: 'image', model: 'm', count: 1 } } as never);
    expect(await dispatchedRatioArg(ctx)).toBe('3:4');
  });

  it('reports the ratio the user picked, unchanged', async () => {
    const ctx = baseCtx({
      gen: { kind: 'image', model: 'm', ratio: '1:1', count: 1 },
    } as never);
    expect(await dispatchedRatioArg(ctx)).toBe('1:1');
  });

  it('reports null when the dispatch sent no ratio at all', async () => {
    // Nothing was measurable, so nothing is known — the slot must fall back
    // rather than be handed a guess dressed up as the request.
    measureRatio.mockResolvedValue(null);
    expect(await dispatchedRatioArg(baseCtx())).toBeNull();
  });

  it("reports a video run's aspect", async () => {
    const ctx = baseCtx({
      gen: { kind: 'video', model: 'm', aspect: '9:16' },
    } as never);
    expect(await dispatchedRatioArg(ctx)).toBe('9:16');
  });
});
