/**
 * Aggregate the things that are actually blocked on the user (A1 + harness P4).
 *
 * They each live somewhere else today: an agent's question sits in the Task
 * Center feed, a tool approval in the TopBar panel, and a finished-but-
 * unconfirmed issue is just another row in the list. Nothing on the Issues
 * page answers "what is waiting on me right now" — this does.
 *
 * Pure: the three inputs are fetched by the caller, this only shapes them.
 */

import type { NeedsInputItem } from '../../services/issuesService';
import type { AILibraryApprovalRequest } from '../../types';
import type { UiIssue } from './types';
import { queuedChip, type CountT } from './issueChips';

export type AttentionType = 'question' | 'approval' | 'review' | 'paused' | 'queued';

export interface AttentionItem {
  type: AttentionType;
  /** Unique across all three sources — the React key and the action target. */
  id: string;
  title: string;
  /** The question / approval reason, when one was given. */
  detail: string | null;
  /** Set for question and review items; approvals are agent-level, not issue-level. */
  issueId?: number;
}

/** Used when the caller has no i18n `t` (pure-function tests). */
const DEFAULT_T: CountT = (_k, o) => o.defaultValue;

export function buildAttentionItems(
  needsInput: NeedsInputItem[] | null | undefined,
  approvals: AILibraryApprovalRequest[] | null | undefined,
  inReviewIssues: UiIssue[] | null | undefined,
  /** harness P4: issues a person paused (`issues.paused_at`) — they wait for a
   *  resume, which is on the person, not the agent. */
  pausedIssues: UiIssue[] | null | undefined = [],
  /** phase 2a §4: issue id → queued-comment count. A paused issue WITH queued
   *  comments is a `queued` item (resume runs them), not a plain `paused`. */
  pendingSummary: Record<string, { count: number }> | null | undefined = {},
  t?: CountT,
): AttentionItem[] {
  const queuedCount = (id: number) => pendingSummary?.[String(id)]?.count ?? 0;
  // Nullish-tolerant on purpose: this strip sits at the top of a whole page,
  // so one upstream feed returning a malformed body must degrade to "nothing
  // waiting", never take the Issues page down with it.
  return [
    ...(needsInput ?? []).map((n): AttentionItem => ({
      type: 'question',
      id: `question:${n.issue_id}`,
      title: n.title,
      detail: n.question ?? null,
      // NeedsInputItem carries issue_id as a string; the list rows key on number.
      issueId: Number(n.issue_id),
    })),
    ...(approvals ?? []).map((a): AttentionItem => ({
      type: 'approval',
      id: a.id,
      title: a.hook_name,
      detail: a.reason ?? null,
    })),
    ...(inReviewIssues ?? []).map((i): AttentionItem => ({
      type: 'review',
      id: `review:${i.id}`,
      title: i.title,
      detail: i.identifier ?? null,
      issueId: i.id,
    })),
    ...(pausedIssues ?? []).map((i): AttentionItem =>
      queuedCount(i.id) > 0
        ? {
            type: 'queued',
            id: `queued:${i.id}`,
            title: i.title,
            detail: queuedChip(queuedCount(i.id), t ?? DEFAULT_T),
            issueId: i.id,
          }
        : {
            type: 'paused',
            id: `paused:${i.id}`,
            title: i.title,
            detail: i.identifier ?? null,
            issueId: i.id,
          },
    ),
  ];
}
