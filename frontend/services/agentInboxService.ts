/**
 * `POST /ai-library/inbox` — deliver into a running agent's inbox (harness P4
 * §1-③). A steer is claimed by the target's root run before its next LLM
 * call; nothing here talks to the run. Ids are strings on the wire.
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

export type InboxTargetKind = 'conversation' | 'issue';
export type InboxDeliverKind = 'steer' | 'answer' | 'budget_reply';

export interface InboxItem {
  id: string;
  target_kind: InboxTargetKind;
  target_id: string;
  user_id: string;
  kind: InboxDeliverKind;
  content: Record<string, unknown>;
  created_at: string;
  claimed_at: string | null;
  claimed_run_id: string | null;
  claimed_turn: number | null;
  claimed_step: number | null;
  expired_at: string | null;
}

export class InboxTargetEndedError extends Error {
  constructor() {
    super('target ended');
    this.name = 'InboxTargetEndedError';
  }
}

const base = () => `${getApiUrl()}/api/v1/ai-library/inbox`;

export async function deliverSteer(
  targetKind: InboxTargetKind,
  targetId: string | number,
  body: string,
): Promise<InboxItem> {
  const res = await fetch(base(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await getAuthHeaders()) },
    body: JSON.stringify({ target_kind: targetKind, target_id: Number(targetId), kind: 'steer', content: { body } }),
  });
  if (res.status === 409) throw new InboxTargetEndedError();
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<InboxItem>;
}

export async function listInbox(
  targetKind: InboxTargetKind,
  targetId: string | number,
  pending = true,
): Promise<InboxItem[]> {
  const qs = new URLSearchParams({ target_kind: targetKind, target_id: String(targetId), pending: String(pending) });
  const res = await fetch(`${base()}?${qs}`, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<InboxItem[]>;
}

/** Phase 2a §4: per-issue count of unclaimed steers for the issues the caller
 *  can see — the "N queued" chips. Keyed by issue id (string, Snowflake). */
export interface PendingSummaryEntry {
  count: number;
  oldestAt: string;
}

export async function fetchPendingSummary(): Promise<Record<string, PendingSummaryEntry>> {
  const res = await fetch(`${base()}/pending-summary?target_kind=issue`, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const rows = (await res.json()) as { target_id: string; count: number; oldest_at: string }[];
  const out: Record<string, PendingSummaryEntry> = {};
  for (const r of rows) out[String(r.target_id)] = { count: Number(r.count), oldestAt: r.oldest_at };
  return out;
}
