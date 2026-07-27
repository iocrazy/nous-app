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

/**
 * Whether `node` belongs to the workflow's active group: the current node
 * itself, or — when the current node runs in a parallel group — any other
 * node sharing its `parallel_group`. `currentNode` is the already-resolved
 * `workflow.nodes.find(n => n.id === workflow.current_node_id)` (or `null`
 * for a workflow with no cursor yet); pass `null` and this always reads
 * false, never throws.
 *
 * Single source of truth for "is this node currently actionable" — shared by
 * `WorkflowSection` (which groups the Overview's node cards) and
 * `WorkspaceStageBoard` (which gates the Stage Board's "Complete stage"
 * action bar) so the two can never silently diverge on the definition.
 */
export function isNodeInActiveGroup(
  node: Pick<ProjectStageNode, 'id' | 'parallel_group'>,
  currentNode: Pick<ProjectStageNode, 'id' | 'parallel_group'> | null,
): boolean {
  if (!currentNode) return false;
  return currentNode.parallel_group != null
    ? node.parallel_group === currentNode.parallel_group
    : node.id === currentNode.id;
}
