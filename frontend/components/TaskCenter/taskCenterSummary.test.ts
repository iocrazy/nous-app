import { describe, it, expect } from 'vitest';
import { summarizeTasks, isActiveStatus, type CountableTask } from './taskCenterSummary';

const T = (status: CountableTask['status']): CountableTask => ({ status });

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
    expect(counts).toEqual({ running: 2, queued: 1, completed: 3, failed: 2 });
  });

  it('folds client-side uploads into running via extraRunning', () => {
    const counts = summarizeTasks([T('pending'), T('completed')], 3);
    expect(counts.running).toBe(3);
    expect(counts.queued).toBe(1);
    expect(counts.completed).toBe(1);
  });

  it('returns all zeros for an empty list', () => {
    expect(summarizeTasks([])).toEqual({ running: 0, queued: 0, completed: 0, failed: 0 });
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
