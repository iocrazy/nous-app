/**
 * Content relay pipelines service (W2b).
 *
 * Mirrors backend/app/api/pipelines_router.py. A pipeline is a FIXED ordered
 * relay of agent steps run against a parent issue; running one fans out step-1
 * as a child issue and the backend auto-advances each step on `done`.
 *
 * Snowflake bigint ids are STRINGS end-to-end (the backend returns them as JSON
 * strings) — never Number() them, precision above 2**53 would be lost.
 */

import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

// ── Types ──────────────────────────────────────────────────────────

export type PipelineRunStatus = 'running' | 'completed' | 'halted' | 'cancelled';

export interface PipelineStep {
  id: string;
  pipeline_id: string;
  step_order: number;
  agent_id: string;
  title_template: string;
  prompt_template: string;
}

/** Step payload for create/update (no server-set ids). */
export interface PipelineStepInput {
  step_order: number;
  agent_id: string;
  title_template: string;
  prompt_template: string;
}

export interface Pipeline {
  id: string;
  team_id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  steps: PipelineStep[];
}

export interface PipelineCreatePayload {
  team_id: number | string;
  name: string;
  description?: string | null;
  enabled?: boolean;
  steps: PipelineStepInput[];
}

export interface PipelineUpdatePayload {
  name?: string;
  description?: string | null;
  enabled?: boolean;
  steps?: PipelineStepInput[];
}

export interface PipelineRun {
  id: string;
  pipeline_id: string;
  parent_issue_id: string;
  current_step: number;
  status: PipelineRunStatus;
  halted_reason: string | null;
  started_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  // Decoration for the active-run strip.
  pipeline_name: string | null;
  total_steps: number | null;
  current_agent_id: string | null;
}

interface PipelineRunListResponse {
  items: PipelineRun[];
}

// ── HTTP ───────────────────────────────────────────────────────────

const _base = `${getApiUrl()}/pipelines`;

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export async function listPipelines(teamId: number | string): Promise<Pipeline[]> {
  const res = await fetch(`${_base}/?team_id=${encodeURIComponent(String(teamId))}`, {
    headers: await getAuthHeaders(),
  });
  return _json<Pipeline[]>(res);
}

export async function getPipeline(pipelineId: string): Promise<Pipeline> {
  const res = await fetch(`${_base}/${pipelineId}`, {
    headers: await getAuthHeaders(),
  });
  return _json<Pipeline>(res);
}

export async function createPipeline(payload: PipelineCreatePayload): Promise<Pipeline> {
  const res = await fetch(`${_base}/`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify(payload),
  });
  return _json<Pipeline>(res);
}

export async function updatePipeline(
  pipelineId: string,
  payload: PipelineUpdatePayload,
): Promise<Pipeline> {
  const res = await fetch(`${_base}/${pipelineId}`, {
    method: 'PATCH',
    headers: await getAuthHeaders(),
    body: JSON.stringify(payload),
  });
  return _json<Pipeline>(res);
}

export async function deletePipeline(pipelineId: string): Promise<void> {
  const res = await fetch(`${_base}/${pipelineId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  return _json<void>(res);
}

/** Start a relay run of `pipelineId` against a parent issue. */
export async function runPipeline(
  pipelineId: string,
  parentIssueId: number | string,
): Promise<PipelineRun> {
  const res = await fetch(`${_base}/${pipelineId}/run`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ parent_issue_id: String(parentIssueId) }),
  });
  return _json<PipelineRun>(res);
}

/** List content-relay runs whose parent is the given issue (newest first). */
export async function listIssuePipelineRuns(
  issueId: number | string,
): Promise<PipelineRun[]> {
  const res = await fetch(`${getApiUrl()}/issues/${issueId}/pipeline-runs`, {
    headers: await getAuthHeaders(),
  });
  const body = await _json<PipelineRunListResponse>(res);
  return body.items;
}
