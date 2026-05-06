/**
 * User-defined cron schedules API client (A 路线 PR #160 / migration 204).
 *
 * Master scheduler workflow scans `user_schedules` every minute and fires
 * due rows. UI can also fire-now for manual one-shot trigger.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

const base = (): string => `${getApiUrl()}/api/v1/schedules`;

export interface ScheduleResponse {
  id: string;
  user_id: string | null;
  name: string;
  cron_expr: string;
  task_type: string;
  payload: Record<string, unknown>;
  lane: string;
  enabled: boolean;
  last_fired_at: string | null;
  next_fire_at: string;
  fire_count: number;
  fail_count: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleCreatePayload {
  name: string;
  cron_expr: string;
  task_type: string;
  payload?: Record<string, unknown>;
  enabled?: boolean;
}

export interface ScheduleUpdatePayload {
  name?: string;
  cron_expr?: string;
  payload?: Record<string, unknown>;
  enabled?: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${base()}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...headers, ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`Schedules API ${res.status}: ${txt || res.statusText}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export const schedulesService = {
  create: (payload: ScheduleCreatePayload): Promise<ScheduleResponse> =>
    request<ScheduleResponse>('', { method: 'POST', body: JSON.stringify(payload) }),

  list: (): Promise<ScheduleResponse[]> => request<ScheduleResponse[]>(''),

  get: (id: string): Promise<ScheduleResponse> => request<ScheduleResponse>(`/${id}`),

  update: (id: string, patch: ScheduleUpdatePayload): Promise<ScheduleResponse> =>
    request<ScheduleResponse>(`/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  remove: (id: string): Promise<void> => request(`/${id}`, { method: 'DELETE' }),

  fireNow: (id: string): Promise<{ schedule_id: string; fired_at: string }> =>
    request(`/${id}/fire-now`, { method: 'POST' }),
};
