import { describe, expect, it } from 'vitest';
import { forkMarksFor } from './useRunForks';

const ev = (seq: number, event_type: string, payload: Record<string, unknown> = {}) =>
  ({ seq, event_type, payload, created_at: '' }) as never;

describe('forkMarksFor', () => {
  it('maps at_seq of a step_start to the folded step key; other seqs are dropped', () => {
    const events = [ev(1, 'user'), ev(2, 'step_start', { turn: 1, step: 1 }), ev(4, 'step_start', { turn: 1, step: 2 }), ev(9, 'turn_end')];
    const forks = [
      { run_id: '701', at_seq: 4, created_at: '', status: 'completed' },
      { run_id: '702', at_seq: 4, created_at: '', status: 'running' },
      { run_id: '703', at_seq: 9, created_at: '', status: 'completed' },
      { run_id: '704', at_seq: 3, created_at: '', status: 'completed' },
    ];
    expect(forkMarksFor(events, forks)).toEqual({ 'step:1:2': ['701', '702'] });
  });
  it('is empty without forks', () => {
    expect(forkMarksFor([ev(2, 'step_start', { turn: 1, step: 1 })], [])).toEqual({});
  });
});
