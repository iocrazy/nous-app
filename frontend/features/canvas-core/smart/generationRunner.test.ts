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
    // The trailing null is the ratio the dispatch sent: GEN2 has no ratio
    // and no source to follow, so nothing was knowable and the slot is left
    // to fall back rather than be handed a guess.
    expect(dispatched).toEqual([['p1', 2, 'image', ['t1', 't2'], null]]);
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

describe('withGenerationRunner — dropped knobs (P4)', () => {
  // P2 puts `dropped_knobs` in the task metadata, right beside `result_url`.
  // Reading one and not the other is how "the backend returns it, the frontend
  // never reads it" happens; the runner reads both at the same terminal point
  // and hands the union to the caller, which puts it on the node.
  //
  // Every run reports TWICE: `[]` at dispatch (the previous run's verdict
  // stops applying the moment a new run starts) and the union at the terminal
  // poll. The assertions below spell out both calls rather than looking only
  // at the last one — the dispatch clear is behaviour, not noise.
  it('reports the knobs the backend ignored', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/cover', dropped_knobs: ['quality'] },
    });

    const dropped: Array<[string, string[]]> = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (id, knobs) => dropped.push([id, knobs]),
    });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: 'ark', count: 1 },
    });

    expect(result.ok).toBe(true);
    expect(dropped).toEqual([
      ['p1', []],
      ['p1', ['quality']],
    ]);
  });

  it('unions the knobs across a fan-out instead of reporting only the last task', async () => {
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    pollGeneration
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/1/cover', dropped_knobs: ['ratio', 'quality'] },
      })
      .mockResolvedValueOnce({
        phase: 'completed',
        metadata: { result_url: '/gm/2/cover', dropped_knobs: ['quality', 'refs'] },
      });

    const dropped: string[][] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (_id, knobs) => dropped.push(knobs),
    });
    await runner({ ...TEXT_CTX, gen: { kind: 'image', model: 'ark', count: 2 } });

    expect(dropped).toEqual([[], ['ratio', 'quality', 'refs']]);
  });

  it('reports an empty list when nothing was dropped, so a stale badge cannot survive a clean run', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/gm/1/cover', dropped_knobs: [] },
    });

    const dropped: string[][] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (_id, knobs) => dropped.push(knobs),
    });
    await runner({ ...TEXT_CTX, gen: { kind: 'image', model: 'jimeng-4', count: 1 } });

    // The SECOND [] is the one that matters: a task reported the field and it
    // was empty. That is an observed clean run, not the dispatch clear.
    expect(dropped).toEqual([[], []]);
  });

  it('still reports drops for a run whose items all failed', async () => {
    // The knobs were dropped at DISPATCH — whether the provider then produced
    // an image is a separate question, and one answer must not hide the other.
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockResolvedValue({
      phase: 'failed',
      error_msg: 'boom',
      metadata: { dropped_knobs: ['ratio'] },
    });

    const dropped: string[][] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (_id, knobs) => dropped.push(knobs),
    });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: 'ark', count: 1 },
    });

    expect(result.ok).toBe(false);
    expect(dropped).toEqual([[], ['ratio']]);
  });

  it('clears the previous run’s verdict at dispatch, before any poll settles', async () => {
    // The window this covers is the whole of the next run: without the
    // dispatch-time clear the node renders `running` beside a verdict about
    // a run that is already over — a claim about work nobody has answered
    // for yet.
    dispatchGenerations.mockResolvedValue(['t1']);
    let settle!: (v: unknown) => void;
    pollGeneration.mockImplementation(
      () => new Promise((res) => { settle = res; }),
    );

    const dropped: string[][] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (_id, knobs) => dropped.push(knobs),
    });
    const done = runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: 'ark', count: 1 },
    });

    // Wait until polling has actually STARTED (so dispatch is behind us) and
    // then assert the clear is already recorded — that is what "before any
    // poll settles" means, and it cannot pass by simply running too early.
    await vi.waitFor(() => expect(pollGeneration).toHaveBeenCalled());
    expect(dropped).toEqual([[]]);

    settle({ phase: 'completed', metadata: { result_url: '/gm/1/cover', dropped_knobs: [] } });
    await done;
    expect(dropped).toEqual([[], []]);
  });

  it('clears it even when the dispatch itself fails', async () => {
    // Nothing downstream of the dispatch runs, so a clear placed at the
    // terminal poll would leave the stale verdict up for good.
    dispatchGenerations.mockRejectedValue(new Error('network down'));

    const dropped: string[][] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (_id, knobs) => dropped.push(knobs),
    });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: 'ark', count: 1 },
    });

    expect(result.ok).toBe(false);
    expect(dropped).toEqual([[]]);
  });

  it('says nothing at the terminal poll when every poll broke — unknown is not “nothing was dropped”', async () => {
    // A broken poll is not a failed task (P1-13): those tasks are still
    // running server-side and nobody has reported their dropped knobs. Firing
    // [] here would write that non-answer down as an observed clean run.
    dispatchGenerations.mockResolvedValue(['t1', 't2']);
    pollGeneration.mockRejectedValue(new Error('poll broke'));

    const dropped: string[][] = [];
    const runner = withGenerationRunner(baseCaller, {
      canvasId: '9',
      onDropped: (_id, knobs) => dropped.push(knobs),
    });
    const result = await runner({
      ...TEXT_CTX,
      gen: { kind: 'image', model: 'ark', count: 2 },
    });

    expect(result.ok).toBe(false);
    // EXACTLY one call: the dispatch clear. No second write at terminal —
    // the badge stays cleared, which reads as "unknown", which it is.
    expect(dropped).toEqual([[]]);
  });
});

describe('every generation run site shows the dropped knobs', () => {
  // A source scan, in the dispatchEffects idiom: the failure this guards is a
  // NEW entry point that constructs the generation runner and quietly omits
  // the badge, which no behavioural test on the existing sites can catch.
  //
  // It walks the WHOLE of canvas-core rather than one directory: a guard that
  // reports clean because it never looked at the file is the shape this repo
  // files under 「健康探针必须可证伪」. And it matches an `onDropped:`
  // property with comments stripped first — a mention in a comment is not
  // wiring, and a guard satisfied by prose guards nothing.
  const SITES = [
    'smart/CanvasComposer.tsx',
    'smart/chainRun.ts',
    'smart/loopRun.ts',
    'smart/regenerate.ts',
  ];

  /** Every non-test .ts/.tsx under canvas-core, path relative to its root. */
  function sourceFiles(root: string, readdirSync: typeof import('node:fs').readdirSync): string[] {
    const out: string[] = [];
    const walk = (dir: string, prefix: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (entry.isDirectory()) walk(`${dir}/${entry.name}`, rel);
        else if (/\.tsx?$/.test(entry.name) && !entry.name.includes('.test.')) out.push(rel);
      }
    };
    walk(root, '');
    return out;
  }

  const stripComments = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');

  it('every withGenerationRunner call site wires onDropped', async () => {
    const { readFileSync, readdirSync } = await import('node:fs');
    const { join } = await import('node:path');
    const root = join(__dirname, '..');
    const constructors = sourceFiles(root, readdirSync).filter(
      (rel) =>
        // The module that DEFINES the wrapper obviously names it.
        rel !== 'smart/generationRunner.ts' &&
        stripComments(readFileSync(join(root, rel), 'utf8')).includes(
          'withGenerationRunner(',
        ),
    );
    expect(
      constructors.sort(),
      'a file constructs the generation runner but is not in the checked set',
    ).toEqual([...SITES].sort());
    for (const rel of SITES) {
      expect(
        stripComments(readFileSync(join(root, rel), 'utf8')),
        `${rel} constructs the generation runner without onDropped — its runs would drop knobs silently`,
      ).toMatch(/\bonDropped\s*:/);
    }
  });

  // ── Terminal-poll sites ───────────────────────────────────────────────────
  // The scan above keys on runner CONSTRUCTION, and that is not where the
  // hole was: `genResume` polls tasks to a terminal phase without building a
  // runner at all, so it read `result_url`, ignored `dropped_knobs`, and the
  // guard reported clean over it for a whole review round.
  //
  // The property that actually matters is "polls a generation task to a
  // terminal phase", so that is what this keys on. Every such file must be
  // classified — reports the field, or is listed with the reason it has
  // nowhere to report it. There is deliberately no third, unclassified
  // state (the tool-descriptor-allowlist idiom): a new terminal-poll site
  // fails this test until someone decides which it is.
  const REPORTS_DROPPED_KNOBS = ['smart/generationRunner.ts', 'smart/genResume.ts'];
  const NO_PROMPT_NODE_TO_REPORT_ON: Record<string, string> = {
    // `last_dropped` lives on PromptNodeData and the badge is rendered by
    // PromptNodeView. These three drive shot / timeline / clip nodes, which
    // have no such field and no badge — reporting there would need a UI that
    // does not exist, not a one-line read.
    'smart/nodes/ShotNodeView.tsx': 'shot node: no prompt-node badge exists',
    'smart/clipRun.ts': 'timeline segment: no prompt-node badge exists',
    'smart/timelineRun.ts': 'timeline node: no prompt-node badge exists',
  };

  it('every terminal-poll site is classified: it reports dropped knobs, or says why it cannot', async () => {
    const { readFileSync, readdirSync } = await import('node:fs');
    const { join } = await import('node:path');
    const root = join(__dirname, '..');
    const pollers = sourceFiles(root, readdirSync).filter(
      (rel) =>
        // The service module DEFINES pollGeneration.
        rel !== 'services/canvasGenerationService.ts' &&
        stripComments(readFileSync(join(root, rel), 'utf8')).includes('pollGeneration('),
    );
    expect(
      pollers.sort(),
      'a file polls a generation task to a terminal phase but is classified neither way — decide whether it can show dropped knobs',
    ).toEqual(
      [...REPORTS_DROPPED_KNOBS, ...Object.keys(NO_PROMPT_NODE_TO_REPORT_ON)].sort(),
    );
    for (const rel of REPORTS_DROPPED_KNOBS) {
      expect(
        stripComments(readFileSync(join(root, rel), 'utf8')),
        `${rel} polls to terminal and reads result_url but never dropped_knobs — the badge would show a clean run for one that dropped knobs`,
      ).toMatch(/\bdropped_knobs\b/);
    }
  });
});
