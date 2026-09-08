/**
 * Pure content derivation for the two list/board row chips (A1).
 *
 * Both read `execution_state`, which the REST list response has carried all
 * along without the UI ever opening it. Note the Realtime caveat: the `issues`
 * publication excludes that column (mig 172), so these labels only refresh on
 * a REST fetch — see mergeRealtimeIssue for why a Realtime UPDATE must not be
 * allowed to blank the field it reads.
 */

import type { UiIssue } from './types';
import { formatElapsed } from './formatElapsed';
import { isIssueLive } from './issuePhase';

function execState(issue: UiIssue): Record<string, unknown> | null {
  return (issue.raw?.execution_state as Record<string, unknown> | null) ?? null;
}

/**
 * Label for the running chip, or null when the issue is not live.
 *
 * "running" alone answers whether an agent is on it; the turn number and
 * elapsed time answer whether it is making progress — the difference between
 * a live task and a stuck one. Both suffixes are best-effort: the turn comes
 * from `execution_state.turn` (written per turn by the dispatch loop, wiped by
 * the terminal set_status) and elapsed from `started_at`.
 */
export function runningChipLabel(issue: UiIssue, now: Date): string | null {
  if (!isIssueLive(issue)) return null;

  const parts = ['running'];
  const turn = Number(execState(issue)?.turn);
  if (Number.isFinite(turn) && turn > 0) parts.push(`turn ${turn}`);

  const startedAt = issue.raw?.started_at;
  const startMs = startedAt ? new Date(startedAt).getTime() : NaN;
  if (Number.isFinite(startMs)) {
    const seconds = Math.max(0, Math.floor((now.getTime() - startMs) / 1000));
    parts.push(formatElapsed(seconds));
  }
  return parts.join(' · ');
}

export interface NeedsReplyChip {
  /** The agent's stated question, used as the chip tooltip. */
  question: string | null;
}

/**
 * Non-null when the agent stopped and asked the user something.
 *
 * The literal `'needs_input'` match is deliberate: an EMPTY_OUTPUT stall parks
 * at the same `needs_followup` status but nobody asked anything, so labelling
 * it "needs your reply" would send the user looking for a question that does
 * not exist.
 */
export function needsReplyChip(issue: UiIssue): NeedsReplyChip | null {
  if (issue.status !== 'needs_followup') return null;
  const state = execState(issue);
  if (state?.agent_outcome !== 'needs_input') return null;
  return { question: (state.outcome_reason as string | null | undefined) ?? null };
}

/**
 * "2 queued" — comments waiting on the issue's inbox for its next run (phase
 * 2a §4). Null for 0 / undefined: a chip that says "0 queued" is noise.
 */
/** The translate function shape `queuedChip` needs: i18next's `t` with a
 *  count option. */
export type CountT = (key: string, opts: { count: number; defaultValue: string }) => string;

export function queuedChip(count: number | undefined | null, t: CountT): string | null {
  if (!count || count <= 0) return null;
  // react-i18next without an instance (unit tests) hands defaultValue back
  // verbatim; once i18next has interpolated, the replace is a no-op.
  return t('issues.queued', { count, defaultValue: '{{count}} queued' }).replace('{{count}}', String(count));
}
