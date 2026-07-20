/**
 * Per-node status presentation for the workflow strip / node card / sidebar
 * Stages group. Hues line up with the Issues STATUS_CONFIG (emerald=done,
 * amber=in progress, purple=review) so the workflow reads as the same system.
 */

import type { WorkflowNodeStatus } from '../../types';

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
