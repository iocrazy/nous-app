/**
 * DBOS workflow REST + SSE client.
 *
 * Mirrors `backend/app/api/workflows_router.py`. Use this for any
 * task that's been routed to a DBOS workflow (parse / download /
 * transcode / analyze_l1 / storyboard_* / write_memory / etc).
 *
 * Legacy unified_tasks Realtime channel keeps working in parallel
 * during the shadow window — pick whichever pipe matches the task's
 * routing-table mode.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { getSupabaseAccessToken } from '../supabaseClient';

// ── Types ──────────────────────────────────────────────────────────

export type DbosWorkflowStatus =
  | 'PENDING'
  | 'ENQUEUED'
  | 'RUNNING'
  | 'SUCCESS'
  | 'ERROR'
  | 'CANCELLED'
  | 'RETRIES_EXCEEDED'
  | 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'
  | string; // forward-compat with new DBOS states

export interface DbosWorkflowSnapshot {
  workflow_id: string | null;
  status: DbosWorkflowStatus | null;
  name: string | null;
  queue_name: string | null;
  created_at: number | null; // unix ms
  updated_at: number | null;
  output: unknown;
  error: string | null;
  executor_id: string | null;
  app_version: string | null;
  steps?: DbosWorkflowStep[];
}

export interface DbosWorkflowStep {
  function_id: number | null;
  function_name: string | null;
  output: unknown;
  error: string | null;
  child_workflow_id: string | null;
}

export const TERMINAL_STATES: ReadonlySet<DbosWorkflowStatus> = new Set([
  'SUCCESS',
  'ERROR',
  'CANCELLED',
  'RETRIES_EXCEEDED',
  'MAX_RECOVERY_ATTEMPTS_EXCEEDED',
]);

// ── REST helpers ───────────────────────────────────────────────────

const _base = (id: string) => `${getApiUrl()}/api/v1/workflows/${encodeURIComponent(id)}`;

export async function getWorkflowStatus(id: string): Promise<DbosWorkflowSnapshot> {
  const res = await fetch(`${_base(id)}/status`, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`status ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function getWorkflowSteps(
  id: string
): Promise<{ workflow_id: string; steps: DbosWorkflowStep[] }> {
  const res = await fetch(`${_base(id)}/steps`, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`steps ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function cancelWorkflow(id: string): Promise<{ status: string }> {
  const res = await fetch(`${_base(id)}/cancel`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`cancel ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function resumeWorkflow(id: string): Promise<{ status: string }> {
  const res = await fetch(`${_base(id)}/resume`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`resume ${res.status}: ${await res.text()}`);
  return res.json();
}

// ── SSE helper ─────────────────────────────────────────────────────

export interface SubscribeOptions {
  /** Also stream the per-step list on each status change. Heavier. */
  includeSteps?: boolean;
  /** Called on each status snapshot push. */
  onStatus: (snap: DbosWorkflowSnapshot) => void;
  /** Workflow reached a terminal state — server has closed the stream. */
  onDone?: () => void;
  /** workflow_id unknown to DBOS (e.g. expired / wrong id). */
  onNotFound?: () => void;
  /** Stream timed out at the 30-min ceiling. */
  onTimeout?: () => void;
  /** Network error or auth failure (EventSource native error event). */
  onError?: (err: Event) => void;
}

/**
 * Subscribe to DBOS workflow status changes via SSE.
 *
 * Browsers' EventSource API can't set Authorization headers, so we
 * pass the Supabase JWT as ?token=. Backend validates it through the
 * same Bearer path as header auth.
 *
 * Returns an unsubscribe function. ALWAYS call it from useEffect's
 * cleanup, otherwise the EventSource keeps polling forever.
 */
export async function subscribeWorkflow(
  id: string,
  opts: SubscribeOptions
): Promise<() => void> {
  const token = await getSupabaseAccessToken();
  if (!token) {
    throw new Error('No Supabase session — cannot open EventSource');
  }

  const params = new URLSearchParams({ token });
  if (opts.includeSteps) params.set('include_steps', 'true');

  const url = `${_base(id)}/events?${params.toString()}`;
  const es = new EventSource(url);

  es.addEventListener('status', (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data);
      opts.onStatus(data as DbosWorkflowSnapshot);
    } catch (e) {
      console.error('[dbos-sse] bad status payload', e);
    }
  });

  es.addEventListener('done', () => {
    opts.onDone?.();
    es.close();
  });

  es.addEventListener('not_found', () => {
    opts.onNotFound?.();
    es.close();
  });

  es.addEventListener('timeout', () => {
    opts.onTimeout?.();
    es.close();
  });

  es.onerror = (err) => {
    opts.onError?.(err);
    // Don't close on transient network errors — EventSource auto-reconnects.
    // Browser closes when readyState=CLOSED, e.g. after auth failure.
  };

  return () => es.close();
}
