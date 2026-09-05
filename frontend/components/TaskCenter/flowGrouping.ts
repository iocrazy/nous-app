import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import type { TaskCounts } from './taskCenterSummary';
import { taskAwaitingInput } from './taskRowPresentation';

// Pure grouping logic for the floating Task Center panel: one user
// submission (parse → download → thumbnail / extract_audio / transcode /
// ai_*) shares a task_tracking.flow_id and should read as ONE task with
// sub-steps, not 6 sibling rows. Kept free of React so the grouping,
// ordering and count semantics are unit tested.

export interface FlowItem {
  kind: 'flow';
  flowId: string;
  /**
   * Pipeline steps ordered by created_at asc (dispatch order), with retries
   * collapsed: one entry per (task_type, subject) carrying the NEWEST attempt.
   */
  steps: UnifiedTask[];
  /** Attempt count keyed by the step's (newest attempt) id; 1 unless retried. */
  attemptCounts: Record<string, number>;
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

/**
 * Retrying a step creates a NEW task_tracking row that the backend joins into
 * the SAME flow (_find_joinable_flow_id), so a summary retried twice arrives
 * as three sibling rows. They are one pipeline step the user asked for once —
 * key them together so the rail draws one circle whose state is the newest
 * attempt's, instead of ✓✕✕✓.
 *
 * The subject (resource, media as fallback) is part of the key on purpose: a
 * batch playlist flow fans out one download row PER resource, and those are
 * genuinely separate steps, not retries of each other. Rows with no subject
 * (the parse root, which runs before any resource exists) key on their own id
 * so nothing unrelated merges.
 */
function attemptKey(t: UnifiedTask): string {
  const subject = t.resource_id ?? t.media_id;
  return subject ? `${t.task_type}::${String(subject)}` : `row::${t.id}`;
}

export interface CollapsedSteps {
  /** One entry per step, newest attempt first-attempt-ordered. */
  steps: UnifiedTask[];
  /** Attempt count keyed by the representative (newest) row's id. */
  attemptCounts: Record<string, number>;
}

/**
 * Collapse a flow's raw task_tracking rows into its steps. THE single
 * definition of "what counts as a retry" — both flow surfaces (the floating
 * panel's FlowStepCard and Settings → Tasks' FlowGroupCard) derive their
 * counts from this, so the two can't drift apart on the question.
 */
export function collapseRetries(rows: UnifiedTask[]): CollapsedSteps {
  const ordered = [...rows].sort((a, b) =>
    (a.created_at || '').localeCompare(b.created_at || ''),
  );

  // Insertion order = each step's FIRST attempt, so collapsing retries keeps
  // the steps in dispatch order even when a retry lands after later steps.
  const attempts = new Map<string, UnifiedTask[]>();
  for (const row of ordered) {
    const key = attemptKey(row);
    const list = attempts.get(key);
    if (list) list.push(row);
    else attempts.set(key, [row]);
  }

  const steps: UnifiedTask[] = [];
  const attemptCounts: Record<string, number> = {};
  for (const list of attempts.values()) {
    const newest = list[list.length - 1];
    steps.push(newest);
    attemptCounts[newest.id] = list.length;
  }
  return { steps, attemptCounts };
}

function buildFlow(flowId: string, rows: UnifiedTask[]): FlowItem {
  const { steps, attemptCounts } = collapseRetries(rows);

  const current =
    steps.find((s) => s.status === 'processing') ??
    steps.find((s) => s.status === 'pending') ??
    null;
  // Panel ordering follows the newest row of ANY attempt — a retry is fresh
  // activity even if the step it belongs to started long ago.
  let latest = '';
  for (const s of rows) {
    if ((s.created_at || '') > latest) latest = s.created_at || '';
  }
  return {
    kind: 'flow',
    flowId,
    steps,
    attemptCounts,
    current,
    hasActive: current !== null,
    doneCount: steps.filter((s) => s.status === 'completed').length,
    failedCount: steps.filter((s) => s.status === 'failed').length,
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
  const counts: TaskCounts = {
    running: 0, queued: 0, completed: 0, failed: 0, waiting: 0,
  };
  for (const item of items) {
    if (item.kind === 'single') {
      switch (item.task.status) {
        case 'processing': counts.running++; break;
        case 'pending':    counts.queued++; break;
        case 'completed':  counts.completed++; break;
        case 'failed':
        case 'cancelled':  counts.failed++; break;
      }
      if (taskAwaitingInput(item.task)) counts.waiting++;
      continue;
    }
    if (item.steps.some((s) => s.status === 'processing')) counts.running++;
    else if (item.hasActive) counts.queued++;
    else if (item.failedCount > 0 || item.steps.some((s) => s.status === 'cancelled'))
      counts.failed++;
    else counts.completed++;
    if (item.steps.some((s) => taskAwaitingInput(s))) counts.waiting++;
  }
  return counts;
}

/** Steps that are terminal — convenience for ring rendering. */
export function isTerminalStatus(status: string): boolean {
  return TERMINAL.has(status);
}


/**
 * A fan-out is N identical siblings ("Generate 3 images"): every step has the
 * same task_type. There is nothing to follow step by step, so the card shows
 * dots only and does not auto-expand the in-flight one — the expanded child
 * read as a second task (2026-09-05). A pipeline (parse → download → …) is
 * not a fan-out and keeps following its current step.
 */
export function isFanOut(flow: FlowItem): boolean {
  if (flow.steps.length < 2) return false;
  const first = flow.steps[0].task_type;
  return flow.steps.every((s) => s.task_type === first);
}


/**
 * What the TopBar badge shows: active work in FLOW units, i.e. what the user
 * calls "a task". One "Generate 3 images" submission is 1, not 3 — the same
 * unit `summarizeFlowItems` gives the panel header, so the badge and the
 * header under it can never disagree again (they did: "2" over "1 queued").
 * In-flight uploads are excluded here because UploadContext draws and counts
 * those itself.
 */
export function countActiveFlowUnits(tasks: UnifiedTask[]): number {
  const backend = tasks.filter(
    (t) => !(t.task_type === 'upload' && (t.status === 'pending' || t.status === 'processing')),
  );
  const c = summarizeFlowItems(groupTasksByFlow(backend));
  return c.running + c.queued;
}
