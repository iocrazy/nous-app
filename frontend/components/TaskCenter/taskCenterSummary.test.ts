import { describe, it, expect } from 'vitest';
import { summarizeTasks, isActiveStatus, type CountableTask } from './taskCenterSummary';

const T = (status: CountableTask['status'], metadata?: CountableTask['metadata']): CountableTask =>
  ({ status, metadata });

describe('summarizeTasks', () => {
  it('counts each status bucket', () => {
    const counts = summarizeTasks([
      T('processing'),
      T('processing'),
      T('pending'),
      T('completed'),
      T('completed'),
      T('completed'),
      T('failed'),
      T('cancelled'),
    ]);
    expect(counts).toEqual({ running: 2, queued: 1, completed: 3, failed: 2, waiting: 0 });
  });

  it('folds client-side uploads into running via extraRunning', () => {
    const counts = summarizeTasks([T('pending'), T('completed')], 3);
    expect(counts.running).toBe(3);
    expect(counts.queued).toBe(1);
    expect(counts.completed).toBe(1);
  });

  it('returns all zeros for an empty list', () => {
    expect(summarizeTasks([])).toEqual({
      running: 0, queued: 0, completed: 0, failed: 0, waiting: 0,
    });
  });

  it('counts active awaiting_input rows as waiting (still running/queued too)', () => {
    const counts = summarizeTasks([
      T('processing', { awaiting_input: { prompt: 'which style?' } }),
      T('processing'),
      // Stale marker on a terminal row must NOT count — it can't consume input.
      T('cancelled', { awaiting_input: { prompt: 'x' } }),
    ]);
    expect(counts.waiting).toBe(1);
    expect(counts.running).toBe(2);
  });
});

describe('isActiveStatus', () => {
  it('treats pending and processing as active', () => {
    expect(isActiveStatus('pending')).toBe(true);
    expect(isActiveStatus('processing')).toBe(true);
  });

  it('treats terminal states as not active', () => {
    expect(isActiveStatus('completed')).toBe(false);
    expect(isActiveStatus('failed')).toBe(false);
    expect(isActiveStatus('cancelled')).toBe(false);
  });
});
