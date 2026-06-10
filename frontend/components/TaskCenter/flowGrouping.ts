import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import type { TaskCounts } from './taskCenterSummary';

// Pure grouping logic for the floating Task Center panel: one user
// submission (parse → download → thumbnail / extract_audio / transcode /
// ai_*) shares a task_tracking.flow_id and should read as ONE task with
// sub-steps, not 6 sibling rows. Kept free of React so the grouping,
// ordering and count semantics are unit tested.

export interface FlowItem {
  kind: 'flow';
  flowId: string;
  /** Pipeline steps ordered by created_at asc (dispatch order). */
  steps: UnifiedTask[];
  /** The step the flow is "at": first processing, else first pending. */
  current: UnifiedTask | null;
  hasActive: boolean;
  doneCount: number;
  failedCount: number;
  /** Newest child created_at — used for panel ordering. */
  latestCreatedAt: string;
}

export interface SingleItem {
  kind: 'single';
  task: UnifiedTask;
}

export type PanelItem = FlowItem | SingleItem;

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

function buildFlow(flowId: string, steps: UnifiedTask[]): FlowItem {
  const ordered = [...steps].sort((a, b) =>
    (a.created_at || '').localeCompare(b.created_at || ''),
  );
  const current =
    ordered.find((s) => s.status === 'processing') ??
    ordered.find((s) => s.status === 'pending') ??
    null;
  let latest = '';
  for (const s of ordered) {
    if ((s.created_at || '') > latest) latest = s.created_at || '';
  }
  return {
    kind: 'flow',
    flowId,
    steps: ordered,
    current,
    hasActive: current !== null,
    doneCount: ordered.filter((s) => s.status === 'completed').length,
    failedCount: ordered.filter((s) => s.status === 'failed').length,
    latestCreatedAt: latest,
  };
}

/**
 * Group tasks into flow items (by flow_id) + standalone singles, sorted by
 * newest activity desc. Tasks without a flow_id (manual single-action
 * triggers, legacy rows, agent runs) stay individual rows — matching the
 * user's mental model: "one submission = one task, unless I clicked it
 * myself".
 */
export function groupTasksByFlow(tasks: UnifiedTask[]): PanelItem[] {
  const flows = new Map<string, UnifiedTask[]>();
  const singles: UnifiedTask[] = [];

  for (const t of tasks) {
    const md = (t.metadata ?? {}) as Record<string, unknown>;
    const flowId = t.flow_id ?? (md.flow_id as string | undefined);
    if (flowId) {
      const list = flows.get(flowId);
      if (list) list.push(t);
      else flows.set(flowId, [t]);
    } else {
      singles.push(t);
    }
  }

  const items: PanelItem[] = [];
  for (const [flowId, steps] of flows) {
    items.push(buildFlow(flowId, steps));
  }
  for (const t of singles) {
    items.push({ kind: 'single', task: t });
  }

  const sortKey = (i: PanelItem) =>
    i.kind === 'flow' ? i.latestCreatedAt : i.task.created_at || '';
  return items.sort((a, b) => sortKey(b).localeCompare(sortKey(a)));
}

/** A flow belongs on the Active tab while ANY step is still in flight. */
export function isActiveItem(item: PanelItem): boolean {
  return item.kind === 'flow'
    ? item.hasActive
    : item.task.status === 'pending' || item.task.status === 'processing';
}

/**
 * Friendly card title. Task titles are worker-prefixed ("Download X",
 * "Parse https://…"); the download title minus its prefix is the video
 * title, and the parse row's subtitle is backfilled with the video title
 * by update_parse_tracking_step — both beat showing a raw URL.
 */
export function flowDisplayTitle(flow: FlowItem): string {
  const download = flow.steps.find((s) => s.task_type === 'download');
  if (download?.title) {
    const stripped = download.title.replace(/^Download\s+/, '').trim();
    if (stripped) return stripped;
  }
  const parse = flow.steps.find((s) => s.task_type === 'parse');
  if (parse?.subtitle && parse.subtitle.trim() && parse.subtitle !== 'Initializing...') {
    return parse.subtitle.trim();
  }
  const first = flow.steps[0];
  return (first?.title || '').trim() || `Flow ${flow.flowId.slice(0, 8)}`;
}

/**
 * Panel-header counts in FLOW units (what the user calls "a task"):
 * a flow is running if any step is processing, queued if active but
 * nothing processing yet, failed if any step failed, completed otherwise.
 */
export function summarizeFlowItems(items: PanelItem[]): TaskCounts {
  const counts: TaskCounts = { running: 0, queued: 0, completed: 0, failed: 0 };
  for (const item of items) {
    if (item.kind === 'single') {
      switch (item.task.status) {
        case 'processing': counts.running++; break;
        case 'pending':    counts.queued++; break;
        case 'completed':  counts.completed++; break;
        case 'failed':
        case 'cancelled':  counts.failed++; break;
      }
      continue;
    }
    if (item.steps.some((s) => s.status === 'processing')) counts.running++;
    else if (item.hasActive) counts.queued++;
    else if (item.failedCount > 0 || item.steps.some((s) => s.status === 'cancelled'))
      counts.failed++;
    else counts.completed++;
  }
  return counts;
}

/** Steps that are terminal — convenience for ring rendering. */
export function isTerminalStatus(status: string): boolean {
  return TERMINAL.has(status);
}
