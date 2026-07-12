// features/canvas-core/smart/stopRun.test.ts
// Cooperative stop (P0-4, Infinite's Run↔Stop): runPrompts stops dispatching
// between prompts; an in-flight generation poll is abandoned via PollStopped
// and the node returns to idle (stop ≠ failure).

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

import { PollStopped } from '../services/canvasGenerationService';
import { withGenerationRunner } from './generationRunner';
import {
  runPrompts,
  runSinglePrompt,
  type PromptCaller,
  type RunHandlers,
  type RunnerContext,
} from './runner';

afterEach(() => vi.clearAllMocks());

const ctxOf = (id: string): RunnerContext => ({
  promptId: id,
  body: 'x',
  provider_slug: '',
  agent_id: null,
});

function recordingHandlers() {
  const statuses: Array<{ id: string; status: string; run_error?: string | null }> = [];
  const handlers: RunHandlers = {
    onStatusChange: (id, status, fields) =>
      statuses.push({ id, status, run_error: fields.run_error }),
  };
  return { handlers, statuses };
}

describe('runPrompts cooperative stop', () => {
  it('stops dispatching between prompts; unstarted prompts stay untouched', async () => {
    const calls: string[] = [];
    let stop = false;
    const caller: PromptCaller = async (ctx) => {
      calls.push(ctx.promptId);
      stop = true; // user hits Stop while p1 runs
      return { ok: true, text: 'done', error: null };
    };
    const { handlers, statuses } = recordingHandlers();
    const results = await runPrompts(
      [ctxOf('p1'), ctxOf('p2'), ctxOf('p3')],
      caller,
      handlers,
      { shouldStop: () => stop },
    );
    expect(calls).toEqual(['p1']);
    expect(results).toHaveLength(1);
    expect(statuses.some((s) => s.id === 'p2')).toBe(false);
    expect(statuses.some((s) => s.id === 'p3')).toBe(false);
  });
});

describe('stopped results return the node to idle', () => {
  it('runSinglePrompt maps stopped:true to idle, not failed', async () => {
    const caller: PromptCaller = async () => ({
      ok: false,
      stopped: true,
      text: '',
      error: 'stopped by user',
    });
    const { handlers, statuses } = recordingHandlers();
    await runSinglePrompt(ctxOf('p1'), caller, handlers);
    const terminal = statuses[statuses.length - 1];
    expect(terminal.status).toBe('idle');
    expect(terminal.run_error).toBeNull();
  });
});

describe('withGenerationRunner stop plumbing', () => {
  it('maps PollStopped into a stopped in-band result', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockRejectedValue(new PollStopped('t1'));
    const base: PromptCaller = async () => ({ ok: true, text: '', error: null });
    const runner = withGenerationRunner(base, {
      canvasId: '9',
      shouldStop: () => true,
    });
    const result = await runner({
      ...ctxOf('p1'),
      gen: { kind: 'image', model: '', count: 1 },
    });
    expect(result.ok).toBe(false);
    expect(result.stopped).toBe(true);
  });

  it('forwards shouldStop into the poll options, resolved per prompt id (P2-9)', async () => {
    dispatchGenerations.mockResolvedValue(['t1']);
    pollGeneration.mockImplementation(
      async (_id: string, opts: { shouldStop?: () => boolean }) => {
        // The poll-level probe must consult the caller's per-prompt check.
        opts.shouldStop?.();
        return { phase: 'completed', metadata: { result_url: '/u1' } };
      },
    );
    const shouldStop = vi.fn(() => false);
    const base: PromptCaller = async () => ({ ok: true, text: '', error: null });
    const runner = withGenerationRunner(base, { canvasId: '9', shouldStop });
    await runner({ ...ctxOf('p1'), gen: { kind: 'image', model: '', count: 1 } });
    expect(shouldStop).toHaveBeenCalledWith('p1');
  });
});
