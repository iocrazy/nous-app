import { describe, it, expect } from 'vitest';
import type { UnifiedTask, TaskStatus } from '../contexts/TaskManagerContext';
import {
  isTerminal,
  isRetryable,
  toggleSelection,
  addAll,
  removeAll,
  partitionForRetry,
} from './taskSelection';

const task = (
  id: string,
  status: TaskStatus,
  error_msg?: string,
): UnifiedTask =>
  ({ id, status, error_msg, task_type: 'transcode' } as unknown as UnifiedTask);

describe('isTerminal', () => {
  it('true for completed/failed/cancelled', () => {
    expect(isTerminal('completed')).toBe(true);
    expect(isTerminal('failed')).toBe(true);
    expect(isTerminal('cancelled')).toBe(true);
  });
  it('false for in-flight and undefined', () => {
    expect(isTerminal('pending')).toBe(false);
    expect(isTerminal('processing')).toBe(false);
    expect(isTerminal(undefined)).toBe(false);
  });
});

describe('isRetryable', () => {
  it('true for transient failed/cancelled', () => {
    expect(isRetryable(task('1', 'failed', 'TimeoutError'))).toBe(true);
    expect(isRetryable(task('2', 'cancelled'))).toBe(true);
  });
  it('false for permanent failures (source gone)', () => {
    expect(isRetryable(task('3', 'failed', 'SodaApiError: no url'))).toBe(false);
  });
  it('false for completed/in-flight', () => {
    expect(isRetryable(task('4', 'completed'))).toBe(false);
    expect(isRetryable(task('5', 'processing'))).toBe(false);
  });
});

describe('toggleSelection', () => {
  it('adds when absent, removes when present, never mutates input', () => {
    const a = new Set<string>();
    const b = toggleSelection(a, 'x');
    expect(a.has('x')).toBe(false); // input untouched
    expect(b.has('x')).toBe(true);
    const c = toggleSelection(b, 'x');
    expect(c.has('x')).toBe(false);
  });
});

describe('addAll / removeAll', () => {
  it('unions and subtracts immutably', () => {
    const base = new Set(['a']);
    expect([...addAll(base, ['b', 'c'])].sort()).toEqual(['a', 'b', 'c']);
    expect([...removeAll(new Set(['a', 'b', 'c']), ['b'])].sort()).toEqual([
      'a',
      'c',
    ]);
    expect([...base]).toEqual(['a']); // untouched
  });
});

describe('partitionForRetry', () => {
  it('splits selection into all / retryable / skipped and drops vanished ids', () => {
    const tasks = [
      task('t1', 'failed', 'TimeoutError'), // retryable
      task('t2', 'completed'), // selected but not retryable
      task('t3', 'failed', 'SodaApiError'), // permanent → not retryable
    ];
    const selected = new Set(['t1', 't2', 't3', 'ghost']);
    const p = partitionForRetry(tasks, selected);
    expect(p.all.sort()).toEqual(['t1', 't2', 't3']); // ghost dropped
    expect(p.retryable).toEqual(['t1']);
    expect(p.skipped).toBe(2);
  });
});
