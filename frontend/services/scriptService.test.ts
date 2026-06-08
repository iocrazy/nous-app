/**
 * Unit tests for scriptService — pins URL/body shapes and envelope
 * unwrap for the { success, data } response convention.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  convertToStoryboard,
  createBranches,
  createScriptAsset,
  createScriptProject,
  deleteScriptAsset,
  deleteScriptProject,
  expandChapter,
  fetchScriptAssets,
  fetchScriptProject,
  fetchScriptProjects,
  generateOutline,
  syncScriptCanvas,
  updateScriptAsset,
  updateScriptProject,
  updateScriptViewport,
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

describe('scriptService canvas + viewport', () => {
  it('syncScriptCanvas POSTs to /canvas/sync', async () => {
    const spy = stubJson({ success: true, data: { chapters: [] } });
    await syncScriptCanvas('s1', {
      added_chapters: [],
      updated_chapters: [],
      deleted_chapter_ids: [],
    });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/projects/s1/canvas/sync',
    );
  });

  it('updateScriptViewport PATCHes viewport', async () => {
    const spy = stubJson({});
    await updateScriptViewport('s1', { x: 0, y: 0, zoom: 1 });
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PATCH');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.zoom).toBe(1);
  });
});

describe('scriptService AI operations', () => {
  it('generateOutline returns task_id', async () => {
    stubJson({ success: true, data: { task_id: 'tk-1' } });
    const result = await generateOutline({
      script_id: 's1',
      premise: 'p',
      chapter_count: 3,
    });
    expect(result.task_id).toBe('tk-1');
  });

  it('expandChapter dispatches async and returns task_id', async () => {
    const spy = stubJson({ success: true, data: { task_id: 'tk-exp' } });
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
    const spy = stubJson({ success: true, data: { task_id: 'tk-br' } });
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

  it('convertToStoryboard dispatches async and returns task_id', async () => {
    stubJson({ success: true, data: { task_id: 'tk-sb' } });
    const result = await convertToStoryboard({
      script_id: 's1',
      chapter_id: 'c1',
    });
    expect(result.task_id).toBe('tk-sb');
  });
});

describe('scriptService assets', () => {
  it('fetchScriptAssets threads asset_type query', async () => {
    const spy = stubJson({ success: true, data: [] });
    await fetchScriptAssets('s1', 'character');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('asset_type=character');
  });

  it('createScriptAsset POSTs to /:script_id/assets', async () => {
    const spy = stubJson({ success: true, data: { id: 'a1', name: 'hero' } });
    await createScriptAsset({
      script_id: 's1',
      asset_type: 'character',
      name: 'hero',
    });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/projects/s1/assets',
    );
  });

  it('updateScriptAsset PUTs to /assets/:id', async () => {
    const spy = stubJson({ success: true, data: { id: 'a1' } });
    await updateScriptAsset('a1', { name: 'v2' });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/projects/assets/a1',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
  });

  it('deleteScriptAsset DELETEs /assets/:id', async () => {
    const spy = stubJson({});
    await deleteScriptAsset('a1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});
