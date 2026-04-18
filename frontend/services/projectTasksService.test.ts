/**
 * Unit tests for projectTasksService — CRUD URL shapes and reorder
 * delegation to updateProjectTask.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createProjectTask,
  deleteProjectTask,
  fetchProjectTasks,
  reorderTask,
  updateProjectTask,
} from './projectTasksService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown, status: number = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('projectTasksService', () => {
  it('fetchProjectTasks unwraps data array', async () => {
    stubJson({ data: [{ id: 't1' }] });
    const rows = await fetchProjectTasks('p-1');
    expect(rows).toHaveLength(1);
  });

  it('fetchProjectTasks returns [] when missing', async () => {
    stubJson({});
    expect(await fetchProjectTasks('p-1')).toEqual([]);
  });

  it('createProjectTask POSTs to /projects/:id/tasks', async () => {
    const spy = stubJson({ data: { id: 't1', title: 'x' } });
    await createProjectTask('p-1', { title: 'x' });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/projects/p-1/tasks',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('createProjectTask throws on empty data', async () => {
    stubJson({});
    await expect(createProjectTask('p-1', { title: 'x' })).rejects.toThrow(
      /Empty/,
    );
  });

  it('updateProjectTask PUTs to /:task_id', async () => {
    const spy = stubJson({ data: { id: 't1', title: 'new' } });
    await updateProjectTask('p-1', 't1', { title: 'new' });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/projects/p-1/tasks/t1',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
  });

  it('deleteProjectTask DELETEs /:task_id', async () => {
    const spy = stubJson({});
    await deleteProjectTask('p-1', 't1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('reorderTask sends status + sort_order via PUT', async () => {
    const spy = stubJson({ data: { id: 't1', status: 'done', sort_order: 5 } });
    await reorderTask('p-1', 't1', 'done', 5);
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.status).toBe('done');
    expect(body.sort_order).toBe(5);
  });
});
