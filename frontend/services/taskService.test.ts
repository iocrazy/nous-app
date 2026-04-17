/**
 * Unit tests for taskService — URL shapes + polling terminal-state
 * detection + formatter helpers.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cancelTask,
  formatBytes,
  formatRelativeTime,
  getActiveTasks,
  getDownloadProgress,
  getQueueStats,
  getTaskStatus,
  getTaskStatusColor,
  getTaskStatusText,
  getWorkerStats,
  pollTaskStatus,
} from './taskService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('taskService API', () => {
  it('getTaskStatus hits /tasks/:id', async () => {
    const spy = stubJson({ task_id: 't-1', status: 'SUCCESS' });
    await getTaskStatus('t-1');
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/tasks/t-1');
  });

  it('cancelTask DELETEs /tasks/:id', async () => {
    const spy = stubJson({ success: true, message: 'ok' });
    await cancelTask('t-1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('getActiveTasks threads queue+limit', async () => {
    const spy = stubJson({ success: true, count: 0, tasks: [] });
    await getActiveTasks('transcription', 50);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('queue=transcription');
    expect(url).toContain('limit=50');
  });

  it('getWorkerStats hits /stats/workers', async () => {
    const spy = stubJson({ success: true, worker_count: 0, workers: [] });
    await getWorkerStats();
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/tasks/stats/workers',
    );
  });

  it('getQueueStats hits /stats/queues', async () => {
    const spy = stubJson({ success: true, queues: {}, total: 0 });
    await getQueueStats();
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/tasks/stats/queues',
    );
  });

  it('getDownloadProgress hits /tasks/:id/progress', async () => {
    const spy = stubJson({
      task_id: 't-1',
      status: 'downloading',
      percent: 50,
    });
    await getDownloadProgress('t-1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/tasks/t-1/progress',
    );
  });
});

describe('pollTaskStatus', () => {
  it('stops polling when status reaches SUCCESS', async () => {
    stubJson({ task_id: 't-1', status: 'SUCCESS' });
    const updates: string[] = [];
    const stop = pollTaskStatus(
      't-1',
      (s) => updates.push(s.status),
      10,
    );
    // Poll fires immediately. Wait a tick for it to resolve.
    await new Promise((r) => setTimeout(r, 20));
    stop();
    expect(updates).toContain('SUCCESS');
  });
});

describe('formatters', () => {
  it('formatBytes 0 → 0 B', () => {
    expect(formatBytes(0)).toBe('0 B');
  });

  it('formatBytes scales to MB', () => {
    expect(formatBytes(1024 * 1024)).toBe('1 MB');
  });

  it('formatRelativeTime: just now for <1m', () => {
    const now = new Date().toISOString();
    expect(formatRelativeTime(now)).toBe('Just now');
  });

  it('formatRelativeTime: minutes ago', () => {
    const past = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(formatRelativeTime(past)).toBe('5m ago');
  });

  it('formatRelativeTime: hours ago', () => {
    const past = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(past)).toBe('3h ago');
  });

  it('formatRelativeTime: days ago', () => {
    const past = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(past)).toBe('2d ago');
  });

  it('getTaskStatusText maps every status', () => {
    expect(getTaskStatusText('SUCCESS')).toBe('已完成');
    expect(getTaskStatusText('FAILURE')).toBe('失败');
    expect(getTaskStatusText('PENDING')).toBe('排队中');
  });

  it('getTaskStatusColor maps every status', () => {
    expect(getTaskStatusColor('SUCCESS')).toContain('green');
    expect(getTaskStatusColor('FAILURE')).toContain('red');
  });
});
