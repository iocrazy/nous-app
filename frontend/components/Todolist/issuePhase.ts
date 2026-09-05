/**
 * Client-side phase of an issue row (harness P4 §1-②), for grouping, Quick
 * chips and the row actions. Same priority as the server's issue.rollup —
 * `paused > waiting_input > running > blocked > done > idle` — derived from
 * the columns the list already carries (no per-row fetch). The detail page
 * uses the server rollup (which also sees the runs); the list uses this.
 */

import type { UiIssue } from './types';

export type IssuePhase = 'paused' | 'waiting_input' | 'running' | 'blocked' | 'done' | 'idle';

export const PHASE_ORDER: IssuePhase[] = ['waiting_input', 'running', 'paused', 'blocked', 'idle', 'done'];

/** The Quick-row chips: the phases a person acts on, in the order they matter. */
export const QUICK_PHASES: IssuePhase[] = ['waiting_input', 'running', 'paused', 'blocked'];

export const PHASE_LABEL_KEY: Record<IssuePhase, string> = {
  waiting_input: 'issues.phase.waiting_input',
  running: 'issues.phase.running',
  paused: 'issues.phase.paused',
  blocked: 'issues.phase.blocked',
  idle: 'issues.phase.idle',
  done: 'issues.phase.done',
};

export const PHASE_FALLBACK: Record<IssuePhase, string> = {
  waiting_input: 'Waiting on you',
  running: 'Running',
  paused: 'Paused',
  blocked: 'Blocked',
  idle: 'To do',
  done: 'Done',
};

const FINISHED = new Set(['done', 'cancelled']);

/**
 * Is an agent turn live on this issue — the ONE list-level answer, shared by
 * the phase, the row chip, the live filter/count and the detail header.
 *
 * `dbos_workflow_id` is written at dispatch and never cleared, so on its own it
 * only says "was dispatched once". The lifecycle (`issue_lifecycle.py`) moves
 * the issue to `in_progress` when the turn starts and to `in_review` /
 * `needs_followup` when it ends, so a workflow id on any other status is
 * history, not liveness. Reading it as "not done yet" produced the
 * `running · 860h` rows on month-old probe issues (2026-09-05) — the server
 * rollup (`/issues/{id}/progress`) already said `idle` for the same rows;
 * this keeps the list, which has no per-row rollup, on the same rule.
 */
export function isIssueLive(issue: UiIssue): boolean {
  const raw = (issue.raw ?? {}) as Record<string, unknown>;
  return !!raw.dbos_workflow_id && issue.status === 'in_progress';
}

export function issuePhase(issue: UiIssue): IssuePhase {
  const raw = (issue.raw ?? {}) as Record<string, unknown>;
  const state = (raw.execution_state as Record<string, unknown> | null) ?? null;
  if (raw.paused_at) return 'paused';
  if (
    (issue.status === 'needs_followup' && state?.agent_outcome === 'needs_input') ||
    (state !== null && 'awaiting_input' in state)
  ) {
    return 'waiting_input';
  }
  if (isIssueLive(issue)) return 'running';
  if (issue.status === 'blocked') return 'blocked';
  if (FINISHED.has(issue.status)) return 'done';
  return 'idle';
}

/** Semantic tone per phase — tokens only (K1), no raw hue classes. */
export const PHASE_TONE: Record<IssuePhase, string> = {
  waiting_input: 'text-warn bg-warn-soft ring-warn-line',
  running: 'text-ok bg-ok-soft ring-ok-line',
  paused: 'text-info bg-info-soft ring-info-line',
  blocked: 'text-danger bg-danger-soft ring-danger-line',
  idle: 'text-ink-400 bg-ink-800/60 ring-ink-700',
  done: 'text-ink-500 bg-ink-800/40 ring-ink-800',
};
