/**
 * issueFlow pure-helper tests — subtask aggregation and due-date bucketing.
 * No network, no React.
 */

import { describe, expect, it } from 'vitest';

import {
  computeSubtaskCounts,
  dueBucket,
  isSubtaskComplete,
  readDueDate,
} from './issueFlow';

function child(parent_id: number | null, status: string) {
  return { parent_id, status };
}

describe('computeSubtaskCounts', () => {
  it('groups children by parent and counts done (done+cancelled) / total', () => {
    const counts = computeSubtaskCounts([
      child(null, 'todo'), // a top-level issue — not a child
      child(1, 'done'),
      child(1, 'cancelled'),
      child(1, 'in_progress'),
      child(2, 'todo'),
    ]);
    expect(counts.get(1)).toEqual({ done: 2, total: 3 }); // done + cancelled
    expect(counts.get(2)).toEqual({ done: 0, total: 1 });
  });

  it('omits parents with no children (no 0/0 placeholder)', () => {
    const counts = computeSubtaskCounts([child(null, 'todo'), child(5, 'todo')]);
    expect(counts.has(999)).toBe(false);
    expect(counts.get(5)).toEqual({ done: 0, total: 1 });
  });

  it('returns an empty map for an empty list', () => {
    expect(computeSubtaskCounts([]).size).toBe(0);
  });

  it('accumulates independently per parent', () => {
    const counts = computeSubtaskCounts([child(1, 'done'), child(1, 'todo')]);
    expect(counts.get(1)).toEqual({ done: 1, total: 2 });
  });
});

describe('isSubtaskComplete', () => {
  it('is true only when every child is terminal', () => {
    expect(isSubtaskComplete({ done: 3, total: 3 })).toBe(true);
    expect(isSubtaskComplete({ done: 2, total: 3 })).toBe(false);
    expect(isSubtaskComplete({ done: 0, total: 0 })).toBe(false);
  });
});

describe('dueBucket', () => {
  const now = new Date('2026-07-20T12:00:00');

  it('returns null when there is no due date (column not shipped yet)', () => {
    expect(dueBucket(null, now)).toBeNull();
    expect(dueBucket(undefined, now)).toBeNull();
  });

  it('returns null on an unparseable value instead of throwing', () => {
    expect(dueBucket('not-a-date', now)).toBeNull();
  });

  it('buckets overdue / soon / normal', () => {
    expect(dueBucket('2026-07-17T12:00:00', now)).toEqual({
      kind: 'overdue',
      label: 'Overdue · Jul 17',
    });
    expect(dueBucket('2026-07-21T10:00:00', now)).toEqual({
      kind: 'soon',
      label: 'Due tomorrow',
    });
    expect(dueBucket('2026-07-25T12:00:00', now)).toEqual({
      kind: 'normal',
      label: 'Jul 25',
    });
  });
});

describe('readDueDate', () => {
  it('reads a string due_date and tolerates its absence', () => {
    expect(readDueDate({ due_date: '2026-07-24' })).toBe('2026-07-24');
    expect(readDueDate({})).toBeUndefined();
    expect(readDueDate(null)).toBeUndefined();
    expect(readDueDate({ due_date: 123 })).toBeUndefined();
  });
});
