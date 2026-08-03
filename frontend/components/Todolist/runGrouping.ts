/**
 * Pure render-layer grouping for consecutive agent runs (A2 timeline).
 *
 * A bounded-continuation dispatch emits one `agent_run` row per turn, so a
 * five-turn task buries the human conversation under five near-identical
 * blocks. This folds a consecutive stretch of >= 2 runs into one summary
 * entry the timeline renders collapsed by default.
 *
 * Boundary semantics:
 *   - `comment` / deliverable rows BREAK a group — once a person speaks, the
 *     next run belongs to a new exchange.
 *   - `system_status` does NOT break a group — the todo → in_progress flips
 *     a dispatch causes are internals of the very run being folded. They stay
 *     in `items` so the expanded view can still show them in order.
 *
 * Side-effect free: reads `IssueMessage[]`, returns a fresh entry list, never
 * mutates the input or its elements (same contract as coalesceSystemStatus).
 */

import type { IssueMessage } from '../../services/issueMessageService';

/** Minimum consecutive runs before folding. One run is not a "group". */
export const RUN_GROUP_THRESHOLD = 2;

export interface RunGroupTotals {
  /** prompt + completion across the group. */
  tokens: number;
  costCents: number;
  durationSeconds: number;
}

export interface RunGroupEntry {
  kind: 'run_group';
  /** Stable React key derived from the span's first & last member. */
  key: string;
  /** The `agent_run` rows only — what the header counts and sums. */
  runs: IssueMessage[];
  /** The whole folded span in original order (runs + interleaved statuses). */
  items: IssueMessage[];
  startedAt: string;
  totals: RunGroupTotals;
  /** True while any member is still in flight — keeps the live cue on the card. */
  anyRunning: boolean;
}

export interface SingleEntry {
  kind: 'single';
  key: string;
  message: IssueMessage;
}

export type TimelineEntry = RunGroupEntry | SingleEntry;

/** meta values arrive as numbers, numeric strings, null or not at all. */
function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function isRunning(msg: IssueMessage): boolean {
  return (msg.meta?.status as string | undefined) === 'running';
}

function totalsOf(runs: IssueMessage[]): RunGroupTotals {
  return runs.reduce<RunGroupTotals>(
    (acc, m) => ({
      tokens: acc.tokens + num(m.meta?.prompt_tokens) + num(m.meta?.completion_tokens),
      costCents: acc.costCents + num(m.meta?.cost_cents),
      durationSeconds: acc.durationSeconds + num(m.duration_seconds),
    }),
    { tokens: 0, costCents: 0, durationSeconds: 0 },
  );
}

/**
 * Fold consecutive `agent_run` stretches (>= RUN_GROUP_THRESHOLD runs) into
 * `run_group` entries; everything else passes through as `single`, order
 * preserved.
 */
export function groupAgentRuns(messages: IssueMessage[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const total = messages.length;
  let i = 0;

  while (i < total) {
    const msg = messages[i];
    if (msg.kind !== 'agent_run') {
      entries.push({ kind: 'single', key: msg.id, message: msg });
      i += 1;
      continue;
    }

    // Extend over runs and the status events between them, then trim back to
    // the last run: a trailing status belongs to whatever comes next, not to
    // the group.
    let end = i;
    let lastRun = i;
    while (end < total && (messages[end].kind === 'agent_run' || messages[end].kind === 'system_status')) {
      if (messages[end].kind === 'agent_run') lastRun = end;
      end += 1;
    }
    const items = messages.slice(i, lastRun + 1);
    const runs = items.filter((m) => m.kind === 'agent_run');

    if (runs.length >= RUN_GROUP_THRESHOLD) {
      entries.push({
        kind: 'run_group',
        key: `run-group-${items[0].id}-${items[items.length - 1].id}`,
        runs,
        items,
        startedAt: runs[0].created_at,
        totals: totalsOf(runs),
        anyRunning: runs.some(isRunning),
      });
      i = lastRun + 1;
      continue;
    }

    // A lone run: emit it alone and resume at the next message, so any status
    // event we looked past is still processed normally.
    entries.push({ kind: 'single', key: msg.id, message: msg });
    i += 1;
  }

  return entries;
}
