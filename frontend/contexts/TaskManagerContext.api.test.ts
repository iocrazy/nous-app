/**
 * The task-manager REST helpers against the backend's real wire shapes.
 *
 * Two defects this pins:
 * - `apiDeleteTask` never read the response, so a delete the server refused
 *   (500) resolved like a success. The Task Center batch runner counts only
 *   throws as failures, so it toasted "N deleted" for rows that still existed.
 * - `apiClearCompleted` likewise ignored the response. (The context's
 *   `clearCompleted` also stopped dropping every terminal row locally — the
 *   server keeps the 50 most recent — and re-reads the list instead; that
 *   half lives in the provider and is not exercised here.)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { TaskTrackingRow } from '../types/api';

vi.mock('../services/parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer t' }),
}));

/** A row exactly as `GET /task-manager/tasks` sends it (every column). */
const ROW: TaskTrackingRow = {
  dbos_workflow_id: '0b8f3c1e-7a44-4d0e-9d0f-3f7d2a1c9e10',
  user_id: '00000000-0000-0000-0000-000000000042',
  task_type: 'download',
  task_kind: 'workflow',
  status: 'failed',
  phase: 'failed',
  title: 'Download 7300000000000000123',
  subtitle: 'Video + Cover',
  progress: 40,
  speed: 1048576,
  total_bytes: 7300000000,
  error_msg: 'boom',
  error_code: 'UNKNOWN',
  resource_id: '7300000000000000001',
  media_id: '7512345678901234567',
  metadata: { retry_count: 2 },
  dedup_key: 'task:download:7512345678901234567',
  subscribers: [],
  cost_cents: 0,
  group_id: null,
  flow_id: '5f0c2b8e-1d3a-4c7e-8b9f-0a1b2c3d4e5f',
  issue_id: 1234567890123,
  agent_id: null,
  parent_task_id: null,
  root_task_id: null,
  inbox_message_id: null,
  heartbeat_at: '2026-09-24T01:02:03.456789+00:00',
  health_status: 'LOST',
  health_notified_at: null,
  max_duration_minutes: null,
  expected_duration_minutes: null,
  do_not_auto_cancel: false,
  created_at: '2026-09-24T01:00:00.123456+00:00',
  started_at: '2026-09-24T01:00:01.123456+00:00',
  completed_at: '2026-09-24T01:02:03.456789+00:00',
  updated_at: '2026-09-24T01:02:03.456789+00:00',
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

/** Production error shell (CLAUDE.md 2026-09-09). */
const errorShell = (status: number) =>
  json(
    {
      success: false,
      error: 'Internal Server Error',
      code: `http_${status}`,
      request_id: 'req_1',
      details: null,
    },
    status,
  );

describe('task-manager REST helpers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('maps a page of real rows onto the UI model', async () => {
    vi.mocked(fetch).mockResolvedValue(
      json({ success: true, data: [ROW], total: 137, page: 3, page_size: 20 }),
    );
    const { fetchTasksPage } = await import('./TaskManagerContext');
    const page = await fetchTasksPage({ page: 3, pageSize: 20 });

    expect(page).toMatchObject({ total: 137, page: 3, pageSize: 20 });
    expect(page.tasks[0].id).toBe(ROW.dbos_workflow_id);
    expect(page.tasks[0].status).toBe('failed');
  });

  it('reads ids / total / capped from the id list', async () => {
    vi.mocked(fetch).mockResolvedValue(
      json({ success: true, ids: ['a', 'b'], total: 2, capped: true }),
    );
    const { fetchMatchingTaskIds } = await import('./TaskManagerContext');
    expect(await fetchMatchingTaskIds({})).toEqual({ ids: ['a', 'b'], total: 2, capped: true });
  });

  it('reads active counts out of the envelope', async () => {
    vi.mocked(fetch).mockResolvedValue(
      json({ success: true, data: { total: 3, by_type: { download: 2, canvas_gen: 1 } } }),
    );
    const { fetchActiveCounts } = await import('./TaskManagerContext');
    expect(await fetchActiveCounts()).toEqual({
      total: 3,
      byType: { download: 2, canvas_gen: 1 },
    });
  });

  it('a refused delete rejects, so the batch runner counts it as failed', async () => {
    vi.mocked(fetch).mockResolvedValue(errorShell(500));
    const { apiDeleteTask } = await import('./TaskManagerContext');
    await expect(apiDeleteTask('wf-1')).rejects.toThrow('500');
  });

  it('a delete of a row already gone (404) resolves', async () => {
    vi.mocked(fetch).mockResolvedValue(errorShell(404));
    const { apiDeleteTask } = await import('./TaskManagerContext');
    await expect(apiDeleteTask('wf-1')).resolves.toBeUndefined();
  });

  it('a successful delete resolves', async () => {
    vi.mocked(fetch).mockResolvedValue(json({ success: true }));
    const { apiDeleteTask } = await import('./TaskManagerContext');
    await expect(apiDeleteTask('wf-1')).resolves.toBeUndefined();
  });

  it('clear-completed reports how many rows the server deleted', async () => {
    vi.mocked(fetch).mockResolvedValue(json({ success: true, cleared: 7 }));
    const { apiClearCompleted } = await import('./TaskManagerContext');
    expect(await apiClearCompleted()).toBe(7);
  });

  it('a refused clear-completed rejects', async () => {
    vi.mocked(fetch).mockResolvedValue(errorShell(500));
    const { apiClearCompleted } = await import('./TaskManagerContext');
    await expect(apiClearCompleted()).rejects.toThrow('500');
  });
});
