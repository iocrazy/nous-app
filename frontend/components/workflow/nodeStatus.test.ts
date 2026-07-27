/**
 * isNodeOverdue (M2-W3-2) — pure/derived overdue predicate for a workflow node.
 * A node is overdue only when its planned_due is strictly before today AND it
 * is neither done nor skipped. `today` is injected so the test is deterministic.
 */
import { describe, expect, it } from 'vitest';

import { isNodeOverdue, unmetDeps } from './nodeStatus';
import type { ProjectStageNode } from '../../types';

const TODAY = '2026-07-20';

function node(over: Partial<ProjectStageNode>): ProjectStageNode {
  return {
    id: '1',
    project_id: '10',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Script',
    sort_order: 0,
    parallel_group: null,
    status: 'in_progress',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...over,
  };
}

describe('isNodeOverdue', () => {
  it('is true when due is before today and node is unfinished', () => {
    expect(isNodeOverdue(node({ planned_due: '2026-07-19' }), TODAY)).toBe(true);
  });

  it('is false when due is today (natural-day, not overdue yet)', () => {
    expect(isNodeOverdue(node({ planned_due: '2026-07-20' }), TODAY)).toBe(false);
  });

  it('is false when due is in the future', () => {
    expect(isNodeOverdue(node({ planned_due: '2026-07-25' }), TODAY)).toBe(false);
  });

  it('is false when there is no due date', () => {
    expect(isNodeOverdue(node({ planned_due: null }), TODAY)).toBe(false);
  });

  it('is false for a done node even if past due', () => {
    expect(
      isNodeOverdue(node({ planned_due: '2026-07-01', status: 'done' }), TODAY),
    ).toBe(false);
  });

  it('is false for a skipped node even if past due (by status or flag)', () => {
    expect(
      isNodeOverdue(node({ planned_due: '2026-07-01', status: 'skipped' }), TODAY),
    ).toBe(false);
    expect(
      isNodeOverdue(node({ planned_due: '2026-07-01', skipped: true }), TODAY),
    ).toBe(false);
  });

  it('compares only the date part of a datetime due value', () => {
    expect(
      isNodeOverdue(node({ planned_due: '2026-07-19T23:59:00+00:00' }), TODAY),
    ).toBe(true);
    expect(
      isNodeOverdue(node({ planned_due: '2026-07-20T00:00:00+00:00' }), TODAY),
    ).toBe(false);
  });
});

/**
 * unmetDeps (M3 PR-J, task J3) — pure/derived local display helper for
 * "Waiting on: X, Y" rows / lock icons. Not the actual advance gate (the
 * server's DEPS_PENDING predicate is), just the same status-satisfied rule
 * mirrored locally so the strip/board can paint ahead of an advance attempt.
 */
describe('unmetDeps', () => {
  it('returns [] when the node has no depends_on', () => {
    const target = node({ id: '1', depends_on: [] });
    expect(unmetDeps([target], target)).toEqual([]);
  });

  it('a done dependency is satisfied — not returned', () => {
    const dep = node({ id: 'dep', status: 'done' });
    const target = node({ id: '1', depends_on: ['dep'] });
    expect(unmetDeps([dep, target], target)).toEqual([]);
  });

  it('a skipped dependency (by status) is satisfied — not returned', () => {
    const dep = node({ id: 'dep', status: 'skipped' });
    const target = node({ id: '1', depends_on: ['dep'] });
    expect(unmetDeps([dep, target], target)).toEqual([]);
  });

  it('a skipped dependency (by flag, non-skipped status) is satisfied — not returned', () => {
    const dep = node({ id: 'dep', status: 'pending', skipped: true });
    const target = node({ id: '1', depends_on: ['dep'] });
    expect(unmetDeps([dep, target], target)).toEqual([]);
  });

  it('a pending/in_progress/in_review dependency is unmet — returned', () => {
    for (const status of ['pending', 'in_progress', 'in_review'] as const) {
      const dep = node({ id: 'dep', status });
      const target = node({ id: '1', depends_on: ['dep'] });
      expect(unmetDeps([dep, target], target)).toEqual([dep]);
    }
  });

  it('a dependency id missing from the nodes list is tolerated, never surfaced as blocking', () => {
    const target = node({ id: '1', depends_on: ['ghost'] });
    expect(unmetDeps([target], target)).toEqual([]);
  });

  it('returns only the unmet subset, preserving depends_on order, when some deps are satisfied', () => {
    const done = node({ id: 'a', status: 'done' });
    const pending = node({ id: 'b', status: 'pending' });
    const skipped = node({ id: 'c', status: 'skipped' });
    const inProgress = node({ id: 'd', status: 'in_progress' });
    const target = node({ id: '1', depends_on: ['a', 'b', 'c', 'd'] });
    expect(unmetDeps([done, pending, skipped, inProgress, target], target)).toEqual([
      pending,
      inProgress,
    ]);
  });
});
