/**
 * Task Flow API client (A 路线 PR #159 / migration 203).
 *
 * A flow is a parent grouping for related tasks (typical: parse → download
 * → transcribe → summary chained from one URL submission). Children attach
 * via task_tracking.flow_id; the trigger keeps parent counters in sync.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

const base = (): string => `${getApiUrl()}/api/v1/flows`;

export type FlowState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | string;

export interface FlowResponse {
  id: string;
  user_id: string;
  name: string;
  state: FlowState;
  cascade_cancel: boolean;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface FlowDetailResponse extends FlowResponse {
  tasks: Array<{
    dbos_workflow_id: string;
    title: string;
    status: string;
    phase: string | null;
    progress: number | null;
    task_type: string;
    task_kind: string;
    error_msg: string | null;
    created_at: string;
    updated_at: string;
  }>;
}

export interface FlowCreatePayload {
  name: string;
  cascade_cancel?: boolean;
  metadata?: Record<string, unknown>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${base()}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...headers, ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`Flow API ${res.status}: ${txt || res.statusText}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export const flowService = {
  create: (payload: FlowCreatePayload): Promise<FlowResponse> =>
    request<FlowResponse>('', { method: 'POST', body: JSON.stringify(payload) }),

  list: (): Promise<FlowResponse[]> => request<FlowResponse[]>(''),

  get: (flowId: string): Promise<FlowDetailResponse> =>
    request<FlowDetailResponse>(`/${flowId}`),

  cancel: (flowId: string): Promise<{ flow_id: string; state: string }> =>
    request(`/${flowId}/cancel`, { method: 'POST' }),

  remove: (flowId: string): Promise<void> => request(`/${flowId}`, { method: 'DELETE' }),
};
