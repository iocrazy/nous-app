import { describe, expect, it } from 'vitest';
import { forkOrigin, timedOutTools } from './runHeader';
import type { AgentRunEvent } from '../../types';

const ev = (seq: number, event_type: string, payload: Record<string, unknown>): AgentRunEvent =>
  ({ seq, event_type, payload, created_at: '' }) as unknown as AgentRunEvent;

describe('forkOrigin', () => {
  it('reads the fork event a forked run opens with (numeric ids as on the wire)', () => {
    // Verbatim shape from production run 347786145852739 seq 1.
    const events = [ev(1, 'fork', { steer: true, at_seq: 5, of_run_id: 347474259567172 }), ev(2, 'user', { content: 'x' })];
    expect(forkOrigin(events)).toEqual({ ofRunId: '347474259567172', atSeq: 5 });
  });
  it('is null for a root run and for a malformed fork payload', () => {
    expect(forkOrigin([ev(1, 'user', { content: 'x' })])).toBeNull();
    expect(forkOrigin([ev(1, 'fork', { of_run_id: null, at_seq: 5 })])).toBeNull();
    expect(forkOrigin([ev(1, 'fork', { of_run_id: 7, at_seq: 0 })])).toBeNull();
  });
});

describe('timedOutTools', () => {
  it('counts only results flagged timed_out and remembers the last tool', () => {
    const events = [
      ev(4, 'tool_call', { tool: 'Skill', result: { timed_out: true, timeout_s: 0.001 } }),
      ev(5, 'tool_call', { tool: 'ResourceFetch', result: { ok: false } }),
      ev(6, 'tool_call', { tool: 'Delegate', result: { timed_out: true } }),
      ev(7, 'tool_call', { tool: 'FinishIssue' }),
    ];
    expect(timedOutTools(events)).toEqual({ count: 2, last: 'Delegate' });
  });
  it('is zero with no timeouts', () => {
    expect(timedOutTools([ev(1, 'tool_call', { tool: 'Skill', result: {} })])).toEqual({ count: 0, last: null });
  });
});
