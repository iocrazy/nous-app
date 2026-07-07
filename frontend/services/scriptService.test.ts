/**
 * Unit tests for scriptService — pins URL/body shapes and envelope
 * unwrap for the { success, data } response convention.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createBranches,
  createScriptProject,
  deleteScriptProject,
  expandChapter,
  fetchScriptProject,
  fetchScriptProjects,
  updateScriptProject,
} from './scriptService';

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

describe('scriptService project CRUD', () => {
  it('fetchScriptProjects threads project_id+page+limit, unwraps items/total', async () => {
    const spy = stubJson({
      success: true,
      data: { items: [{ id: 's1' }], total: 1 },
    });
    const out = await fetchScriptProjects('p-1', 2, 10);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('project_id=p-1');
    expect(url).toContain('page=2');
    expect(url).toContain('limit=10');
    expect(out.data).toHaveLength(1);
    expect(out.total).toBe(1);
  });

  it('fetchScriptProjects handles missing data gracefully', async () => {
    stubJson({ success: true });
    const out = await fetchScriptProjects('p-1');
    expect(out.data).toEqual([]);
    expect(out.total).toBe(0);
  });

  it('fetchScriptProject merges project + chapters', async () => {
    stubJson({
      success: true,
      data: {
        project: { id: 's1', name: 'hello' },
        chapters: [{ id: 'c1' }],
      },
    });
    const result = await fetchScriptProject('s1');
    expect(result.id).toBe('s1');
    expect(result.chapters).toHaveLength(1);
  });

  it('createScriptProject POSTs payload and returns data', async () => {
    const spy = stubJson({ success: true, data: { id: 's1', name: 'x' } });
    const created = await createScriptProject({
      project_id: 'p-1',
      name: 'x',
    });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(created.id).toBe('s1');
  });

  it('updateScriptProject PUTs to /:id', async () => {
    const spy = stubJson({ success: true, data: { id: 's1', name: 'renamed' } });
    await updateScriptProject('s1', { name: 'renamed' });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/projects/s1',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
  });

  it('deleteScriptProject DELETEs', async () => {
    const spy = stubJson({});
    await deleteScriptProject('s1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});

describe('scriptService AI operations', () => {
  it('expandChapter dispatches async and returns task_id', async () => {
    const spy = stubJson({ success: true, task_id: 'tk-exp' });
    const result = await expandChapter({
      script_id: 's1',
      chapter_id: 'c1',
      title: 't',
      summary: 's',
    });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/expand-chapter',
    );
    expect(result.task_id).toBe('tk-exp');
  });

  it('createBranches dispatches async and returns task_id', async () => {
    const spy = stubJson({ success: true, task_id: 'tk-br' });
    const result = await createBranches({
      script_id: 's1',
      chapter_id: 'c1',
      title: 't',
      summary: 's',
      branch_count: 2,
      branch_type: 'choice',
    });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/create-branches',
    );
    expect(result.task_id).toBe('tk-br');
  });
});
