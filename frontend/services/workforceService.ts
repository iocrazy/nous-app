/**
 * Workforce dashboard API client.
 *
 * Wraps `/api/v1/workforce/board` — single fat endpoint that returns
 * everything the Workforce page renders: persistent agents, worker
 * state rows, queue depth counts, recent runs, recent state history.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

const base = (): string => `${getApiUrl()}/api/v1/workforce`;

export interface WorkforceWorkerRow {
  agent_id: string;
  state: 'idle' | 'working' | 'waiting_for_other' | 'blocked' | 'paused' | 'terminated' | string;
  current_task_id: string | null;
  state_changed_at: string;
  heartbeat_at: string | null;
}

export interface WorkforceQueueCounts {
  inbox_unread: number;
  inbox_reading: number;
  outbox_undelivered: number;
}

export interface WorkforceRecentRun {
  id: string;
  agent_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | string;
  trigger: string;
  started_at: string;
  ended_at: string | null;
  cost_cents: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  model: string | null;
}

export interface WorkforceAgentEntry {
  id: string;
  slug: string;
  name: string;
  icon: string | null;
  model: string | null;
  persistent: boolean;
  paused_reason: string | null;
  worker: WorkforceWorkerRow | null;
  queue: WorkforceQueueCounts;
  recent_runs: WorkforceRecentRun[];
}

export interface WorkforceStateHistoryRow {
  agent_slug: string;
  from_state: string | null;
  to_state: string;
  trigger: string;
  task_id: string | null;
  changed_at: string;
}

export interface WorkforceBoard {
  agents: WorkforceAgentEntry[];
  recent_state_history: WorkforceStateHistoryRow[];
}

export interface WorkforceInboxRow {
  id: string;
  sender_kind: string;
  sender_user_id: string | null;
  sender_agent_id: string | null;
  message_type: string;
  payload: Record<string, unknown> | null;
  status: string;
  priority: number | null;
  created_at: string;
  processed_at: string | null;
  reply_to_message_id: string | null;
  dedup_key: string | null;
}

export interface WorkforceOutboxRow {
  id: string;
  recipient_kind: string;
  recipient_user_id: string | null;
  recipient_agent_id: string | null;
  message_type: string;
  payload: Record<string, unknown> | null;
  task_id: string | null;
  delivered: boolean;
  delivered_at: string | null;
  created_at: string;
}

export interface WorkforceDetailRun {
  id: string;
  status: string;
  trigger: string;
  model: string | null;
  provider: string | null;
  started_at: string;
  ended_at: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  cost_cents: number | null;
  input_summary: string | null;
  output_summary: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface WorkforceAgentDetail {
  agent: {
    id: string;
    slug: string;
    name: string;
    icon: string | null;
    model: string | null;
    persistent: boolean;
    paused_reason: string | null;
  };
  inbox: WorkforceInboxRow[];
  outbox: WorkforceOutboxRow[];
  runs: WorkforceDetailRun[];
}

async function postJson<T>(
  path: string,
  body: Record<string, unknown> | null = null,
): Promise<T> {
  const resp = await fetch(`${base()}${path}`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}

export const workforceService = {
  async getAgentDetail(slug: string): Promise<WorkforceAgentDetail> {
    const resp = await fetch(
      `${base()}/agents/${encodeURIComponent(slug)}/detail`,
      { headers: await getAuthHeaders() },
    );
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
    return resp.json();
  },

  async getBoard(): Promise<WorkforceBoard> {
    const resp = await fetch(`${base()}/board`, {
      headers: await getAuthHeaders(),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
    return resp.json();
  },

  async pauseAgent(slug: string, reason?: string): Promise<void> {
    await postJson(`/agents/${encodeURIComponent(slug)}/pause`, { reason });
  },

  async resumeAgent(slug: string): Promise<void> {
    await postJson(`/agents/${encodeURIComponent(slug)}/resume`);
  },

  async clearInbox(slug: string): Promise<{ cleared: number }> {
    return postJson(`/agents/${encodeURIComponent(slug)}/clear-inbox`);
  },

  async cancelTask(taskId: string): Promise<void> {
    await postJson(`/tasks/${encodeURIComponent(taskId)}/cancel`);
  },
};
