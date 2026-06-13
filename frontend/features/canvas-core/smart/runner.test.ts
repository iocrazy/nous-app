import { describe, expect, it, vi } from 'vitest';

import {
  failingMockRunner,
  mockRunner,
  runPrompts,
  runSinglePrompt,
  type PromptStatus,
  type RunHandlers,
  type RunnerContext,
  type RunnerResult,
} from './runner';

interface RecordedStatus {
  promptId: string;
  status: PromptStatus;
  fields: Record<string, unknown>;
}

function recordingHandlers(): {
  statuses: RecordedStatus[];
  results: Array<{ promptId: string; result: RunnerResult }>;
  handlers: RunHandlers;
} {
  const statuses: RecordedStatus[] = [];
  const results: Array<{ promptId: string; result: RunnerResult }> = [];
  return {
    statuses,
    results,
    handlers: {
      onStatusChange: (promptId, status, fields) =>
        statuses.push({ promptId, status, fields }),
      onResult: (promptId, result) => results.push({ promptId, result }),
      now: () => '2026-06-10T12:00:00Z',
    },
  };
}

const ctx = (id: string): RunnerContext => ({
  promptId: id,
  body: 'do thing',
  provider_slug: '',
  agent_id: null,
});

describe('runSinglePrompt — success path', () => {
  it('transitions idle → queued → running → succeeded with timestamps', async () => {
    const { statuses, handlers } = recordingHandlers();
    const result = await runSinglePrompt(ctx('p1'), mockRunner, handlers);
    expect(result.ok).toBe(true);
    const sequence = statuses.map((s) => s.status);
    expect(sequence).toEqual(['queued', 'running', 'succeeded']);
    expect(statuses[1].fields.run_started_at).toBe('2026-06-10T12:00:00Z');
    expect(statuses[2].fields.run_finished_at).toBe('2026-06-10T12:00:00Z');
    expect(statuses[2].fields.run_error).toBeNull();
  });

  it('passes the result back to onResult', async () => {
    const { results, handlers } = recordingHandlers();
    await runSinglePrompt(ctx('p1'), mockRunner, handlers);
    expect(results).toHaveLength(1);
    expect(results[0].result.text).toBe('mock(p1)');
  });
});

describe('runSinglePrompt — failure path', () => {
  it('marks failed + carries run_error', async () => {
    const { statuses, handlers } = recordingHandlers();
    const result = await runSinglePrompt(ctx('p1'), failingMockRunner, handlers);
    expect(result.ok).toBe(false);
    const last = statuses[statuses.length - 1];
    expect(last.status).toBe('failed');
    expect(last.fields.run_error).toMatch(/mock failure/);
  });

  it('catches thrown errors from the caller', async () => {
    const { statuses, handlers } = recordingHandlers();
    const thrower = vi.fn(async () => {
      throw new Error('exploded');
    });
    const result = await runSinglePrompt(ctx('p1'), thrower, handlers);
    expect(result.ok).toBe(false);
    expect(result.error).toBe('exploded');
    expect(statuses[statuses.length - 1].status).toBe('failed');
  });
});

describe('runPrompts — sequence', () => {
  it('runs in the order provided', async () => {
    const { results, handlers } = recordingHandlers();
    await runPrompts(
      ['p1', 'p2', 'p3'].map(ctx),
      mockRunner,
      handlers,
    );
    expect(results.map((r) => r.promptId)).toEqual(['p1', 'p2', 'p3']);
  });

  it('stops at first failure by default', async () => {
    const { results, statuses, handlers } = recordingHandlers();
    let n = 0;
    const failOnSecond = vi.fn(async (c: RunnerContext) => {
      n += 1;
      if (n === 2) return { ok: false, text: '', error: 'bad' };
      return { ok: true, text: c.promptId, error: null };
    });
    await runPrompts(['p1', 'p2', 'p3'].map(ctx), failOnSecond, handlers);
    expect(results.map((r) => r.promptId)).toEqual(['p1', 'p2']);
    expect(statuses.some((s) => s.promptId === 'p3')).toBe(false);
  });

  it('continueOnFailure runs everything', async () => {
    const { results, handlers } = recordingHandlers();
    await runPrompts(['p1', 'p2', 'p3'].map(ctx), failingMockRunner, handlers, {
      continueOnFailure: true,
    });
    expect(results.map((r) => r.promptId)).toEqual(['p1', 'p2', 'p3']);
    expect(results.every((r) => !r.result.ok)).toBe(true);
  });
});
