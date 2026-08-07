/**
 * Issue row field helpers — pure data for the todo-list row/detail extra
 * columns (Subtasks, Due), plus the single-point project-flow source loader
 * for the issue detail context bar (`loadProjectFlow`, at the bottom).
 *
 * Everything above the "project-flow source" divider is pure (no React, no
 * network); only `loadProjectFlow` touches the network, and it is the ONE place
 * the context bar decides between the workflow node chain and the legacy SOP
 * catalog — consumers just render whatever `{name,index,total}` it returns.
 *
 * Subtask counts are aggregated from the SAME issue list the view already
 * loaded, grouping children by `parent_id`. That in-memory aggregate is
 * deliberate: issue ids are 19-digit snowflakes typed as JS `number` on the
 * REST path (a lossy boundary), while a direct Supabase read surfaces them as
 * strings — cross-referencing the two could mismatch. Counting parent and child
 * from the one REST-loaded set keeps the ids internally consistent (both went
 * through the identical transform), which is why `listSubIssues` groups the
 * same way. Callers MUST pass the UNFILTERED list so display filters never
 * undercount.
 *
 * Due-date bucketing is pre-baked: the `due_date` column ships with the
 * workflow session's migration, so `readDueDate` stays blank until then.
 */

// ── subtask counts ────────────────────────────────────────────────────────────

export interface SubtaskCount {
  /** Children in a terminal state (done or cancelled). */
  done: number;
  /** Total children. */
  total: number;
}

// A child is "finished" on the same terminal set the pipeline relay uses.
const TERMINAL_STATUSES = new Set(['done', 'cancelled']);

interface ChildIssueLike {
  parent_id: number | null;
  status: string;
}

/**
 * Group children by parent and count done/total. A parent with zero children
 * is absent from the map (its row renders nothing — no `0/0`). Pass the FULL
 * unfiltered issue list so a display filter can't hide children.
 */
export function computeSubtaskCounts(
  issues: ChildIssueLike[],
): Map<number, SubtaskCount> {
  const counts = new Map<number, SubtaskCount>();
  for (const issue of issues) {
    if (issue.parent_id == null) continue;
    const prev = counts.get(issue.parent_id) ?? { done: 0, total: 0 };
    counts.set(issue.parent_id, {
      done: prev.done + (TERMINAL_STATUSES.has(issue.status) ? 1 : 0),
      total: prev.total + 1,
    });
  }
  return counts;
}

/** True when every child is terminal (drives the emerald "all done" tint). */
export function isSubtaskComplete(count: SubtaskCount): boolean {
  return count.total > 0 && count.done >= count.total;
}

// ── due-date bucketing (pre-baked; the column lands with the workflow session) ─

export type DueKind = 'normal' | 'soon' | 'overdue';

export interface DueInfo {
  kind: DueKind;
  label: string;
}

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

function monthDay(d: Date): string {
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Bucket an issue's due date for display, or null to render nothing. Returns
 * null when `dueDate` is absent — the `due_date` column does not exist yet
 * (it arrives with the workflow session's migration), so every current payload
 * simply omits it and the UI stays blank until then. Also null on an unparseable
 * value rather than throwing.
 *
 * Buckets: overdue (past) → rose "Overdue · Jul 17"; soon (within 24h) → amber
 * "Due tomorrow"; otherwise normal grey "Jul 24".
 */
export function dueBucket(
  dueDate: string | null | undefined,
  now: Date = new Date(),
): DueInfo | null {
  if (!dueDate) return null;
  const due = new Date(dueDate);
  if (Number.isNaN(due.getTime())) return null;
  const delta = due.getTime() - now.getTime();
  if (delta < 0) return { kind: 'overdue', label: `Overdue · ${monthDay(due)}` };
  if (delta <= DAY_MS) return { kind: 'soon', label: 'Due tomorrow' };
  return { kind: 'normal', label: monthDay(due) };
}

/** Read a due date off a raw issue row without assuming the column exists. */
export function readDueDate(raw: unknown): string | null | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const value = (raw as Record<string, unknown>).due_date;
  return typeof value === 'string' ? value : undefined;
}

// ── project-flow source (issue detail context bar) ────────────────────────────
//
// A project that runs a workflow instance presents its flow from the node chain
// (WorkspaceTopBar / Overview / Sidebar already do); the issue context bar's
// progress ring must read the SAME source. `loadProjectFlow` is that single
// switch point: workflow nodes when the project has one, null otherwise (the
// legacy SOP catalog fallback was retired in G3 — a No-workflow project's ring
// simply hides).

import { fetchProjectWorkflow } from '../../services/workflowService';
import type { ProjectWorkflow } from '../../types';

/** Flow read-out for the context bar's progress ring. */
export interface ProjectFlow {
  /** Current stage/node display name. */
  name: string;
  /** 1-based position of the current stage/node. */
  index: number;
  /** Total stages/nodes in the flow. */
  total: number;
}

/**
 * Derive the flow read-out from a workflow instance, or null when the project
 * has no workflow / no resolvable current node. Nodes arrive ordered by
 * sort_order; "current" is the node whose id matches `current_node_id` (the
 * shape carries no per-node `current` flag).
 */
export function deriveWorkflowFlow(
  workflow: ProjectWorkflow | null | undefined,
): ProjectFlow | null {
  if (!workflow?.has_workflow) return null;
  const nodes = workflow.nodes ?? [];
  if (nodes.length === 0) return null;
  const idx = nodes.findIndex((n) => n.id === workflow.current_node_id);
  if (idx < 0) return null;
  return { name: nodes[idx].name, index: idx + 1, total: nodes.length };
}

/**
 * Single-point loader for the issue context bar's flow ring. Workflow projects
 * read the node chain; a No-workflow project resolves to null (the legacy SOP
 * catalog fallback was retired in G3 — the ring simply hides). Best-effort —
 * any failure also resolves to null.
 */
export async function loadProjectFlow(
  projectId: string,
  episodeId?: string,
): Promise<ProjectFlow | null> {
  try {
    const workflow = await fetchProjectWorkflow(projectId, episodeId).catch(() => null);
    return deriveWorkflowFlow(workflow);
  } catch {
    return null;
  }
}
