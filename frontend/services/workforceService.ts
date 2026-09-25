/**
 * Workforce dashboard API client.
 *
 * Wraps `/api/v1/workforce/board` — single fat endpoint that returns
 * everything the Workforce page renders: persistent agents, worker
 * state rows, queue depth counts, recent runs, recent state history.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

import type {
  DelegateTaskLookup,
  WorkforceAgentDetail,
  WorkforceBoard,
  WorkforceInboxClearResult,
} from '../types/api';

export type {
  DelegateOutboxResponse,
  DelegateTaskLookup,
  DelegateTaskRow,
  WorkforceAgentDetail,
  WorkforceAgentEntry,
  WorkforceBoard,
  WorkforceDetailRun,
  WorkforceInboxRow,
  WorkforceOutboxRow,
  WorkforceQueueCounts,
  WorkforceRecentRun,
  WorkforceStateHistoryRow,
  WorkforceWorkerRow,
} from '../types/api';

const base = (): string => `${getApiUrl()}/api/v1/workforce`;

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

  async clearInbox(slug: string): Promise<WorkforceInboxClearResult> {
    return postJson(`/agents/${encodeURIComponent(slug)}/clear-inbox`);
  },

  async cancelTask(taskId: string): Promise<void> {
    await postJson(`/tasks/${encodeURIComponent(taskId)}/cancel`);
  },

  /**
   * Look up the agent_tasks row + sub-agent's outbox response for a
   * Delegate dispatch identified by ``inbox_message_id`` (returned by
   * the Delegate tool). Powers the chat sub-task cards' live status.
   *
   * Returns task=null when the recipient hasn't picked up the inbox
   * row yet (still 'queued'); outbox_response=null when the sub-agent
   * hasn't replied yet.
   */
  async getTaskByInbox(
    inboxMessageId: string,
  ): Promise<DelegateTaskLookup> {
    const resp = await fetch(
      `${base()}/tasks/by-inbox/${encodeURIComponent(inboxMessageId)}`,
      { headers: await getAuthHeaders() },
    );
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`${resp.status}: ${text}`);
    }
    return resp.json();
  },
};

/** Possible values of agent_tasks.lifecycle_status. */
export type TaskLifecycle =
  | 'queued'
  | 'assigned'
  | 'in_progress'
  | 'waiting_for_other'
  | 'blocked'
  | 'done'
  | 'failed'
  | 'cancelled';

const TASK_LIFECYCLES: ReadonlySet<string> = new Set<TaskLifecycle>([
  'queued',
  'assigned',
  'in_progress',
  'waiting_for_other',
  'blocked',
  'done',
  'failed',
  'cancelled',
]);

/** The wire types `lifecycle_status` as a plain string (it is the task row's
 *  `phase`); narrow it here instead of casting at each use. An unknown value
 *  reads as "no lifecycle yet". */
export function asTaskLifecycle(value: string | null | undefined): TaskLifecycle | null {
  return value != null && TASK_LIFECYCLES.has(value) ? (value as TaskLifecycle) : null;
}

/** Lifecycle states that won't change anymore — stop polling / unsub. */
export const TERMINAL_LIFECYCLES: ReadonlySet<TaskLifecycle> = new Set([
  'done',
  'failed',
  'cancelled',
]);
