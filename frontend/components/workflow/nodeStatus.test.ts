/**
 * isNodeOverdue (M2-W3-2) — pure/derived overdue predicate for a workflow node.
 * A node is overdue only when its planned_due is strictly before today AND it
 * is neither done nor skipped. `today` is injected so the test is deterministic.
 */
import { describe, expect, it } from 'vitest';

import { isNodeOverdue } from './nodeStatus';
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
