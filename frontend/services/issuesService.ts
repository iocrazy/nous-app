/**
 * Issues service — top-level user-visible "thing" entity (PR-D6).
 *
 * Mirrors backend/app/api/issues_router.py. Pair with
 * useDbosWorkflowStatus(snapshot.dbos_workflow_id) to live-stream
 * dispatch progress via the D4 SSE endpoint.
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

// ── Types ──────────────────────────────────────────────────────────

export type IssueStatus =
  | 'backlog'
  | 'todo'
  | 'in_progress'
  | 'in_review'
  | 'blocked'
  | 'needs_followup'
  | 'done'
  | 'cancelled';

export type IssuePriority = 'critical' | 'high' | 'medium' | 'low';

// Four mirrors of this enum exist: DB CHECK (mig 367), models/reviews.py,
// schemas/issue.py, and here — change all four together.
export type IssueOriginKind =
  | 'manual'
  | 'chat_delegate'
  | 'celery_pipeline'
  | 'agent_dispatch'
  | 'routine'
  | 'escalation'
  | 'project_stage'
  | 'publish'
  | 'pipeline';

export interface Issue {
  id: number;
  issue_number: number;
  identifier: string;
  title: string;
  description: string | null;
  status: IssueStatus;
  priority: IssuePriority;
  team_id: number | null;
  project_id: number | null;
  parent_id: number | null;
  assignee_user_id: string | null;
  assignee_agent_id: string | null;
  origin_kind: IssueOriginKind;
  origin_id: string | null;
  origin_fingerprint: string;
  billing_code: string | null;
  created_by_user_id: string | null;
  created_by_agent_id: string | null;
  dbos_workflow_id: string | null;
  /** Conversation the agent's turns were written into. BIGINT Snowflake, but
   *  str-serialized by the backend (unlike the numeric ids above) because it
   *  only ever ends up in a URL. Null until the first dispatch backfills one. */
  ai_session_id: string | null;
  execution_locked_at: string | null;
  execution_state: Record<string, unknown> | null;
  /** harness P4 (mig 453): target-level pause + budget. Absent on older payloads. */
  paused_at?: string | null;
  budget_cents?: number | null;
  request_depth: number;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  hidden_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface IssueListResponse {
  items: Issue[];
  total: number;
  limit: number;
  offset: number;
}

export interface IssueCreatePayload {
  title: string;
  description?: string;
  status?: IssueStatus;
  priority?: IssuePriority;
  // Snowflake BIGINTs — accept string to preserve precision past 2^53.
  team_id?: number | string;
  project_id?: number | string;
  parent_id?: number;
  assignee_user_id?: string;
  assignee_agent_id?: string;
  origin_kind?: IssueOriginKind;
  origin_id?: string;
  origin_fingerprint?: string;
  billing_code?: string;
}

export interface IssueUpdatePayload {
  /** harness P4 §1-⑤: integer cents, >= 0; NULL = unlimited (use clear_budget). */
  budget_cents?: number;
  clear_budget?: boolean;
  title?: string;
  description?: string;
  priority?: IssuePriority;
  assignee_user_id?: string;
  assignee_agent_id?: string;
  project_id?: number;
  team_id?: number;
  billing_code?: string;
  hidden_at?: string;
}

export interface IssueListFilters {
  status?: IssueStatus;
  // Snowflake BIGINTs — accept string to preserve precision past 2^53.
  project_id?: number | string;
  team_id?: number | string;
  include_hidden?: boolean;
  limit?: number;
  offset?: number;
}

/** One issue parked at `needs_followup` with the agent waiting on a human
 * answer (Spec-4 needs_input first-class). Mirrors
 * backend/app/schemas/issue.py::NeedsInputItem — feeds the Task Center
 * "Needs your answer" section (Task 3). All BIGINT ids ride as strings
 * (Snowflake-precision convention), unlike the plain `Issue` shape above. */
export interface NeedsInputItem {
  issue_id: string;
  title: string;
  question: string | null;
  project_id: string | null;
  team_id: string | null;
  asked_at: string;
  /** Which agent is parked on this question — the feed's agent dimension. */
  assignee_agent_id: string | null;
  /** Human identifier ("MH-7"); the detail route is keyed by it, not by id. */
  identifier: string | null;
  /** Phase 2a: the typed question the issue was parked with (from
   *  execution_state.awaiting_input). Absent on plain needs_input parks. */
  question_id?: string | null;
  kind?: string | null;
  options?: { label: string; description?: string | null }[];
  allow_free_text?: boolean;
}

export interface NeedsInputListResponse {
  items: NeedsInputItem[];
}

// ── REST helpers ───────────────────────────────────────────────────

const _base = `${getApiUrl()}/api/v1/issues`;

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export async function createIssue(payload: IssueCreatePayload): Promise<Issue> {
  const res = await fetch(`${_base}/`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(payload),
  });
  return _json<Issue>(res);
}

export async function listIssues(
  filters: IssueListFilters = {}
): Promise<IssueListResponse> {
  const params = new URLSearchParams();
  if (filters.status) params.set('status', filters.status);
  if (filters.project_id !== undefined)
    params.set('project_id', String(filters.project_id));
  if (filters.team_id !== undefined) params.set('team_id', String(filters.team_id));
  if (filters.include_hidden) params.set('include_hidden', 'true');
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  if (filters.offset !== undefined) params.set('offset', String(filters.offset));

  const url = params.toString() ? `${_base}/?${params}` : `${_base}/`;
  const res = await fetch(url, { headers: await getAuthHeaders() });
  return _json<IssueListResponse>(res);
}

export async function getIssue(issueId: number): Promise<Issue> {
  const res = await fetch(`${_base}/${issueId}`, {
    headers: await getAuthHeaders(),
  });
  return _json<Issue>(res);
}

/** GET /issues/paused — issues a person paused (phase 2a §2/§4). Ids are
 *  strings (Snowflake), same convention as NeedsInputItem. */
export interface PausedIssueItem {
  issue_id: string;
  identifier: string | null;
  title: string;
  paused_at: string;
  team_id: string | null;
  project_id: string | null;
  assignee_agent_id: string | null;
}

export interface PausedListResponse {
  items: PausedIssueItem[];
  /** More are paused than the page holds (server page = 50). */
  has_more: boolean;
}

export async function listPaused(): Promise<PausedListResponse> {
  const res = await fetch(`${_base}/paused`, { headers: await getAuthHeaders() });
  const out = await _json<Partial<PausedListResponse>>(res);
  return { items: out.items ?? [], has_more: out.has_more === true };
}

/** Typed rejection of the pause / resume controls (phase 2a §2). `code` is
 *  the server's `detail.code` (`already_paused`, `not_paused`,
 *  `run_state_unavailable`, …) or `http_<status>` when the body carried no
 *  code — the UI maps codes to copy and never shows the raw body. */
export class IssueControlError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = 'IssueControlError';
    this.code = code;
    this.status = status;
  }
}

/** Like `_json`, but a non-2xx becomes an IssueControlError carrying the
 *  server's `detail.code` (FastAPI `{detail: {code, message}}` or a plain
 *  string detail). */
async function _controlJson<T>(res: Response): Promise<T> {
  if (res.ok) return res.status === 204 ? (undefined as unknown as T) : res.json();
  let code = `http_${res.status}`;
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    const detail = body?.detail;
    if (detail && typeof detail === 'object') {
      const d = detail as { code?: unknown; message?: unknown };
      if (typeof d.code === 'string' && d.code) code = d.code;
      if (typeof d.message === 'string' && d.message) message = d.message;
    } else if (typeof detail === 'string' && detail) {
      message = detail;
    }
  } catch (err) {
    console.error('[issuesService] control error body was not JSON', err);
  }
  throw new IssueControlError(code, res.status, message);
}

/** POST /issues/{id}/pause (phase 2a §2): stamps paused_at and asks the live
 *  root run to stop at its next step boundary. Rejects with IssueControlError
 *  (409 already_paused, 503 run_state_unavailable). */
export async function pauseIssue(
  issueId: number,
): Promise<{ issue_id: string; paused_at: string; run_id: string | null }> {
  const res = await fetch(`${_base}/${issueId}/pause`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return _controlJson(res);
}

/** POST /issues/{id}/resume: clears paused_at; re-dispatches when there is
 *  queued work. `reason` says what happened (dispatched / withdrawn / running
 *  / parked / cleared). Rejects with IssueControlError (409 not_paused). */
export async function resumeIssue(
  issueId: number,
): Promise<{ issue_id: string; dispatched: boolean; reason: string; workflow_id: string | null; run_id: string | null }> {
  const res = await fetch(`${_base}/${issueId}/resume`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return _controlJson(res);
}

export async function listNeedsInput(): Promise<NeedsInputListResponse> {
  const res = await fetch(`${_base}/needs-input`, {
    headers: await getAuthHeaders(),
  });
  return _json<NeedsInputListResponse>(res);
}

/**
 * Fetch sub-issues (children where parent_id=parentIssueId) using the
 * existing list endpoint with a parent_id filter wired client-side.
 * Backend doesn't have a dedicated endpoint yet; we filter client-side.
 */
export async function listSubIssues(parentIssueId: number, limit = 200): Promise<Issue[]> {
  const all = await listIssues({ limit });
  return all.items.filter((i) => i.parent_id === parentIssueId);
}

export async function getIssueByIdentifier(identifier: string): Promise<Issue> {
  const res = await fetch(
    `${_base}/by-identifier/${encodeURIComponent(identifier)}`,
    { headers: await getAuthHeaders() }
  );
  return _json<Issue>(res);
}

export async function updateIssue(
  issueId: number,
  patch: IssueUpdatePayload
): Promise<Issue> {
  const res = await fetch(`${_base}/${issueId}`, {
    method: 'PATCH',
    headers: await getAuthHeaders(),
    body: JSON.stringify(patch),
  });
  return _json<Issue>(res);
}

export async function transitionIssueStatus(
  issueId: number,
  status: IssueStatus,
  reason?: string
): Promise<Issue> {
  const res = await fetch(`${_base}/${issueId}/transition`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ status, reason }),
  });
  return _json<Issue>(res);
}

export type DispatchBlockedReason =
  | 'no_assignee'
  | 'dbos_disabled'
  | 'terminal_status'
  | 'already_running';

/** What POST /dispatch would do — predicted server-side; never re-derive it here. */
export interface DispatchPreview {
  will_start: boolean;
  agent_id: string | null;
  blocked_reason: DispatchBlockedReason | null;
}

export async function getDispatchPreview(issueId: number): Promise<DispatchPreview> {
  const res = await fetch(`${_base}/${issueId}/dispatch-preview`, {
    headers: await getAuthHeaders(),
  });
  return _json<DispatchPreview>(res);
}

export async function dispatchIssue(issueId: number): Promise<Issue> {
  const res = await fetch(`${_base}/${issueId}/dispatch`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return _json<Issue>(res);
}

export async function deleteIssue(issueId: number): Promise<void> {
  const res = await fetch(`${_base}/${issueId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  await _json<void>(res);
}

// UI label/order/color maps live in components/Todolist/issueConfig.ts
// (single source of truth). The service layer stays presentation-free.


// ── issue.rollup (harness P4 §1-②) ──────────────────────────────────────────

export type IssuePhase = 'paused' | 'waiting_input' | 'running' | 'blocked' | 'done' | 'idle';

export interface IssueProgressRun {
  id: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  model: string | null;
  error_code: string | null;
  cost_cents: number;
  ended: { reason: string } | null;
  step: { done: number; total: number; label: string | null } | null;
}

export interface IssueProgress {
  issue_id: string;
  status: string;
  phase: IssuePhase;
  paused_at: string | null;
  current_run: {
    id: string;
    status: string;
    started_at: string | null;
    model: string | null;
    /** `agent_runs.metadata_json.view` — read through runView.ts selectors. */
    view: Record<string, unknown>;
    cost: Record<string, unknown>;
  } | null;
  runs: IssueProgressRun[];
  sub_issues: { total: number; done: number; items: { id: string; identifier: string | null; title: string | null; status: string | null }[] };
  inbox_pending: number;
  budget: { budget_cents: number | null; spent_cents: number; pct: number | null; state: 'ok' | 'warn' | 'over' };
  origin: { kind: string; origin_id?: string | null; [k: string]: unknown };
  execution_state: Record<string, unknown>;
  computed_at: string;
}

/** `GET /issues/{id}/progress` — computed from the runs, never from
 *  execution_state alone (the MH-1 drift). */
export async function getIssueProgress(issueId: number): Promise<IssueProgress> {
  const res = await fetch(`${_base}/${issueId}/progress`, { headers: await getAuthHeaders() });
  return _json<IssueProgress>(res);
}
