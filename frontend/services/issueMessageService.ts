/**
 * Issue Messages service — paperclip-style chat thread per issue (A8.3).
 *
 * Mirrors backend/app/api/issue_messages_router.py.
 * Pair with a Realtime subscription on the `issue_messages` table for
 * live updates (filter: issue_id=eq.<id>).
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

// ── Types ──────────────────────────────────────────────────────────

export type IssueMessageKind = 'comment' | 'agent_run' | 'system_status';

export type AgentLivenessState = 'running' | 'silent' | 'stuck' | 'dead' | 'cancelled';

export interface IssueMessage {
  id: string;
  issue_id: number;
  kind: IssueMessageKind;
  author_user_id: string | null;
  author_agent_id: string | null;
  body: string | null;
  meta: Record<string, unknown>;
  duration_seconds: number | null;
  agent_run_id: string | null;
  /** Optional: surfaced when the chat row was emitted by the agent_runs
   * bridge trigger (mig 206) AND the run carried a liveness state in
   * meta. Null for plain comments. */
  liveness_state?: AgentLivenessState | null;
  from_status: string | null;
  to_status: string | null;
  created_at: string;
}

export interface IssueMessageList {
  messages: IssueMessage[];
  total: number;
}

export type IssueMessageAttachment =
  | { kind: 'image' | 'video' | 'pdf'; url: string; mime?: string | null }
  | {
      kind: 'resource_ref';
      resource_id: string;
      name: string;
      mime: string;
      scope: { type: 'personal' | 'team'; id: string };
    };

export interface IssueMessagePostPayload {
  body: string;
  agent_id?: string | null;
  attachments?: IssueMessageAttachment[];
}

export interface IssueMessagePostResponse {
  comment: IssueMessage;
  agent_run: IssueMessage | null;
}

// ── REST helpers ───────────────────────────────────────────────────

const _base = `${getApiUrl()}/api/v1/issues`;

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export async function listIssueMessages(issueId: number): Promise<IssueMessageList> {
  const res = await fetch(`${_base}/${issueId}/messages`, {
    headers: await getAuthHeaders(),
  });
  return _json<IssueMessageList>(res);
}

export async function postIssueMessage(
  issueId: number,
  payload: IssueMessagePostPayload,
): Promise<IssueMessagePostResponse> {
  const res = await fetch(`${_base}/${issueId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await getAuthHeaders()) },
    body: JSON.stringify(payload),
  });
  return _json<IssueMessagePostResponse>(res);
}

/**
 * Dev/demo helper: flip an issue-scoped agent_run from running →
 * completed with a sample summary. The DB triggers fan out the chat
 * row update via Realtime.
 */
export async function simulateAgentRunComplete(
  issueId: number,
  runId: string,
  outputSummary?: string,
): Promise<{ run_id: string; status: string; summary_preview: string }> {
  const url = `${_base}/${issueId}/agent-runs/${runId}/simulate-complete`;
  const params = outputSummary ? `?output_summary=${encodeURIComponent(outputSummary)}` : '';
  const res = await fetch(`${url}${params}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });
  return _json(res);
}
