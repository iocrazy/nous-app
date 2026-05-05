/**
 * DBOS workflow REST + SSE client.
 *
 * Mirrors `backend/app/api/workflows_router.py`. Used for execution-truth
 * operations (cancel/restart/SSE event stream) — those go to DBOS native
 * APIs because they actually stop / fork the running workflow.
 *
 * Reading task lists / lifecycle status, however, goes through
 * `task_tracking` Realtime + `/api/v1/task-manager/tasks` REST (see
 * TaskManagerContext). The PG trigger trg_mirror_dbos_lifecycle keeps
 * task_tracking in sync with dbos.workflow_status automatically.
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
  input?: unknown;
  output?: unknown;
  error: string | null;
  executor_id: string | null;
  app_version: string | null;
  authenticated_user?: string | null;
  steps?: DbosWorkflowStep[];
}

export interface ListWorkflowsResponse {
  workflows: DbosWorkflowSnapshot[];
  total: number;
  offset: number;
  limit: number;
}

export interface ListWorkflowsOptions {
  name?: string; // workflow name filter (e.g. "parse_workflow")
  status?: DbosWorkflowStatus;
  limit?: number; // 1..200, default 50
  offset?: number;
  sortDesc?: boolean; // default true (newest first)
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

export async function restartWorkflow(
  id: string
): Promise<{ status: string; original_workflow_id: string; new_workflow_id: string }> {
  const res = await fetch(`${_base(id)}/restart`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`restart ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function listWorkflows(
  opts: ListWorkflowsOptions = {}
): Promise<ListWorkflowsResponse> {
  const params = new URLSearchParams();
  if (opts.name) params.set('name', opts.name);
  if (opts.status) params.set('workflow_status', opts.status);
  if (opts.limit != null) params.set('limit', String(opts.limit));
  if (opts.offset != null) params.set('offset', String(opts.offset));
  if (opts.sortDesc != null) params.set('sort_desc', String(opts.sortDesc));
  const qs = params.toString();
  const url = `${getApiUrl()}/api/v1/workflows${qs ? `?${qs}` : ''}`;
  const res = await fetch(url, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`list ${res.status}: ${await res.text()}`);
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
 * pass auth as a query parameter. We use a one-shot 30s ticket
 * (?ticket=) instead of the raw Supabase JWT (?token=) so the JWT
 * never lands in nginx / uvicorn / Sentry access logs.
 *
 * Returns an unsubscribe function. ALWAYS call it from useEffect's
 * cleanup, otherwise the EventSource keeps polling forever.
 */
export async function subscribeWorkflow(
  id: string,
  opts: SubscribeOptions
): Promise<() => void> {
  const session = await getSupabaseAccessToken();
  if (!session) {
    throw new Error('No Supabase session — cannot open EventSource');
  }
  const { wsTicketService } = await import('./wsTicketService');
  const { ticket } = await wsTicketService.acquire();
  const params = new URLSearchParams({ ticket });
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
