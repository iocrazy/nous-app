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
  /** NULL since mig 461: a one-shot `issue_wakeup` has a `next_fire_at` and
   *  no recurrence at all. */
  cron_expr: string | null;
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
  // W2a autopilot hardening (mig 370)
  timezone: string;
  consecutive_fails: number;
  paused_at: string | null;
  pause_reason: string | null;
  skipped_count: number;
  stale_after_minutes: number;
}

/** One row of `GET /api/v1/issues/{id}/schedules` — the wake-ups pointing at
 *  an issue plus the routine that created it. Narrower than ScheduleResponse:
 *  that endpoint projects only what the panel draws. */
export interface IssueScheduleItem {
  id: string;
  task_type: string;
  fire_at: string | null;
  cron_expr: string | null;
  text: string;
  created_by: string;
  enabled: boolean;
  /** Why a stopped row stopped (fired_once / issue_terminal / stale / …). */
  pause_reason: string | null;
}

export interface ScheduleCreatePayload {
  name: string;
  cron_expr: string;
  task_type: string;
  payload?: Record<string, unknown>;
  enabled?: boolean;
  timezone?: string;
}

export interface ScheduleUpdatePayload {
  name?: string;
  cron_expr?: string;
  payload?: Record<string, unknown>;
  enabled?: boolean;
  timezone?: string;
}

/** A refusal the caller can BRANCH on, rather than a string to print.
 *  Mirrors IssueControlError / RunForkRejectedError. */
export class ScheduleRejectedError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = 'ScheduleRejectedError';
    this.code = code;
    this.status = status;
  }
}

/**
 * Turn a non-2xx into a typed error. Production wraps every HTTPException in
 * the ErrorResponse envelope (`app/core/exceptions.py`):
 * `{success, error, code: "http_<status>", request_id, details: <exc.detail>}`
 * — and `schedules_router._bad_request` always puts the typed `{code,message}`
 * under `details`. A bare FastAPI `{detail: …}` is accepted too.
 *
 * The raw body never leaves this function: it carries a request_id and the
 * whole internal envelope, which is not something to paint into a popover.
 */
async function reject(res: Response): Promise<never> {
  let code = `http_${res.status}`;
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown; details?: unknown; error?: unknown };
    const detail = body?.details ?? body?.detail;
    if (detail && typeof detail === 'object') {
      const d = detail as { code?: unknown; message?: unknown };
      if (typeof d.code === 'string' && d.code) code = d.code;
      if (typeof d.message === 'string' && d.message) message = d.message;
    } else if (typeof detail === 'string' && detail) {
      message = detail;
    } else if (typeof body?.error === 'string' && body.error) {
      message = body.error;
    }
  } catch (err) {
    // Not JSON at all (a gateway's HTML, say) — keep the status line.
    console.error('[schedulesService] error body was not JSON', err);
  }
  throw new ScheduleRejectedError(code, res.status, message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${base()}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...headers, ...(init?.headers ?? {}) },
  });
  if (!res.ok) return reject(res);
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

/**
 * Arm a one-shot wake-up on an issue. NOT `create`: that one is the cron
 * shape and requires `name` + `cron_expr`, neither of which a one-shot has.
 * `issue_id` goes in as a NUMBER — issue ids are JSON numbers everywhere in
 * `/issues/*`, and the backend matches on `payload->>'issue_id'`.
 */
export async function createIssueWakeup(
  issueId: number,
  opts: { fireAt: Date | string; text: string },
): Promise<ScheduleResponse> {
  const fire_at = typeof opts.fireAt === 'string' ? opts.fireAt : opts.fireAt.toISOString();
  return request<ScheduleResponse>('', {
    method: 'POST',
    body: JSON.stringify({
      task_type: 'issue_wakeup',
      fire_at,
      payload: { issue_id: issueId, text: opts.text, once: true },
    }),
  });
}

/** Everything timed on one issue. Lives under `/issues/…`, not `/schedules/…`,
 *  so it does not go through `request`. */
export async function listIssueSchedules(issueId: number): Promise<IssueScheduleItem[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/issues/${issueId}/schedules`, { headers });
  if (!res.ok) throw new Error(`Issue schedules API ${res.status}`);
  return ((await res.json()) as { items?: IssueScheduleItem[] }).items ?? [];
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

  // Clear an auto-paused (or disabled) routine: re-enable, reset the
  // consecutive-failure run, drop pause metadata, recompute next_fire_at.
  resume: (id: string): Promise<ScheduleResponse> =>
    request<ScheduleResponse>(`/${id}/resume`, { method: 'POST' }),
};
