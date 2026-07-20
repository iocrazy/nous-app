/**
 * Per-node status presentation for the workflow strip / node card / sidebar
 * Stages group. Hues line up with the Issues STATUS_CONFIG (emerald=done,
 * amber=in progress, purple=review) so the workflow reads as the same system.
 */

import type { ProjectStageNode, WorkflowNodeStatus } from '../../types';

export interface NodeStatusMeta {
  /** Solid dot / tick hue (Tailwind bg-*). */
  dot: string;
  /** Text hue for labels (Tailwind text-*). */
  text: string;
}

export const NODE_STATUS_CONFIG: Record<WorkflowNodeStatus, NodeStatusMeta> = {
  pending: { dot: 'bg-ink-600', text: 'text-ink-400' },
  in_progress: { dot: 'bg-amber-400', text: 'text-amber-400' },
  in_review: { dot: 'bg-purple-400', text: 'text-purple-400' },
  done: { dot: 'bg-emerald-500', text: 'text-emerald-500' },
  skipped: { dot: 'bg-ink-700', text: 'text-ink-600' },
};

export const NODE_STATUS_LABEL: Record<WorkflowNodeStatus, string> = {
  pending: 'Pending',
  in_progress: 'In Progress',
  in_review: 'In Review',
  done: 'Done',
  skipped: 'Skipped',
};

/** Local calendar day as an ISO `YYYY-MM-DD` string (natural-day comparison). */
function localToday(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/**
 * Whether a node's planned due date has passed (M2-W3-2, pure/derived). A node
 * is overdue when its `planned_due` is strictly before today AND it is not yet
 * finished (`done`) or parked (`skipped`). ISO date strings compare
 * lexicographically, so no Date math is needed. `today` is injectable for tests.
 */
export function isNodeOverdue(
  node: Pick<ProjectStageNode, 'planned_due' | 'status' | 'skipped'>,
  today: string = localToday(),
): boolean {
  if (!node.planned_due) return false;
  if (node.status === 'done' || node.status === 'skipped' || node.skipped) {
    return false;
  }
  // planned_due may carry a time component from the API — compare the date part.
  return node.planned_due.slice(0, 10) < today;
}
