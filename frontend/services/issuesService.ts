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
  execution_locked_at: string | null;
  execution_state: Record<string, unknown> | null;
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
