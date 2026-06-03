import { describe, it, expect } from 'vitest';
import { sortTasks } from './taskDisplay';
import type { UnifiedTask } from '../contexts/TaskManagerContext';

/**
 * Regression: sortTasks must order by the actual instant, not by raw
 * timestamp string. created_at reaches the client in MIXED formats —
 * PostgREST timestamptz ("...+00:00", variable fractional digits) and
 * optimistic placeholders via new Date().toISOString() ("...Z"). A
 * string localeCompare interleaves these (newest-first showed older rows
 * mid-list). Comparing parsed epochs fixes it regardless of format/offset.
 */
function task(id: string, created_at: string, updated_at?: string): UnifiedTask {
  return {
    id,
    user_id: 'u1',
    task_type: 'download',
    status: 'completed',
    title: id,
    progress: 100,
    metadata: {},
    created_at,
    updated_at,
  } as UnifiedTask;
}

describe('sortTasks', () => {
  // Instant order (newest→oldest): C (09:00:00.5Z) > B (05:00:00Z) > A (02:00:00Z).
  // A is written with a +08:00 offset so its RAW STRING ("...T10:00...") sorts
  // lexicographically FIRST even though it's the oldest instant — the exact
  // trap a string compare falls into.
  const A = task('A', '2026-06-03T10:00:00+08:00'); // instant 02:00:00Z (oldest)
  const B = task('B', '2026-06-03T05:00:00Z');      // instant 05:00:00Z
  const C = task('C', '2026-06-03T09:00:00.5Z');    // instant 09:00:00.5Z (newest)

  it('created_desc orders by instant, not string (newest first)', () => {
    const ids = sortTasks([A, B, C], 'created_desc').map((t) => t.id);
    expect(ids).toEqual(['C', 'B', 'A']);
  });

  it('created_asc orders by instant (oldest first)', () => {
    const ids = sortTasks([C, B, A], 'created_asc').map((t) => t.id);
    expect(ids).toEqual(['A', 'B', 'C']);
  });

  it('handles mixed Z / +00:00 / fractional-digit formats', () => {
    const x = task('x', '2026-06-03T08:00:06.000Z');       // 6.000s
    const y = task('y', '2026-06-03T08:00:05.999999+00:00'); // 5.999999s (older)
    const ids = sortTasks([y, x], 'created_desc').map((t) => t.id);
    expect(ids).toEqual(['x', 'y']);
  });

  it('updated_desc falls back to created_at and sorts by instant', () => {
    const p = task('p', '2026-06-03T01:00:00Z', '2026-06-03T10:00:00+08:00'); // updated 02:00Z
    const q = task('q', '2026-06-03T01:00:00Z', '2026-06-03T05:00:00Z');      // updated 05:00Z (newer)
    const ids = sortTasks([p, q], 'updated_desc').map((t) => t.id);
    expect(ids).toEqual(['q', 'p']);
  });

  it('does not mutate the input array', () => {
    const input = [A, B, C];
    sortTasks(input, 'created_desc');
    expect(input.map((t) => t.id)).toEqual(['A', 'B', 'C']);
  });
});
