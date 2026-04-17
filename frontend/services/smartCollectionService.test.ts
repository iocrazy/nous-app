/**
 * Unit tests for smartCollectionService — CRUD endpoints + helper
 * rule builders.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildCollectionRules,
  createSmartCollection,
  deleteSmartCollection,
  fetchSmartCollections,
  getSmartCollection,
  getSmartCollectionVideos,
  initPresetCollections,
  presetRules,
  refreshSmartCollection,
  updateSmartCollection,
} from './smartCollectionService';

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

describe('smart collection CRUD', () => {
  it('fetchSmartCollections unwraps collections array', async () => {
    stubJson({ collections: [{ id: 'c1' }] });
    const rows = await fetchSmartCollections();
    expect(rows).toHaveLength(1);
  });

  it('fetchSmartCollections returns [] when missing', async () => {
    stubJson({});
    expect(await fetchSmartCollections()).toEqual([]);
  });

  it('getSmartCollection hits /:id', async () => {
    const spy = stubJson({ id: 'c1' });
    await getSmartCollection('c1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/collections/c1',
    );
  });

  it('createSmartCollection POSTs payload', async () => {
    const spy = stubJson({ id: 'c1' });
    await createSmartCollection({
      name: 'x',
      rules: { match: 'all', conditions: [] },
    });
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('updateSmartCollection PUTs to /:id', async () => {
    const spy = stubJson({ id: 'c1' });
    await updateSmartCollection('c1', { name: 'new' });
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
  });

  it('deleteSmartCollection DELETEs /:id', async () => {
    const spy = stubJson({});
    await deleteSmartCollection('c1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('getSmartCollectionVideos threads page/page_size/use_cache', async () => {
    const spy = stubJson({
      media: [],
      total: 0,
      page: 2,
      page_size: 10,
      collection_id: 'c1',
      collection_name: 'x',
    });
    await getSmartCollectionVideos('c1', 2, 10, false);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('page=2');
    expect(url).toContain('page_size=10');
    expect(url).toContain('use_cache=false');
  });

  it('refreshSmartCollection POSTs /:id/refresh', async () => {
    const spy = stubJson({ message: 'ok', video_count: 5 });
    await refreshSmartCollection('c1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/collections/c1/refresh',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('initPresetCollections returns [] when missing', async () => {
    stubJson({});
    expect(await initPresetCollections()).toEqual([]);
  });
});

describe('rule builders', () => {
  it('buildCollectionRules echoes match+conditions', () => {
    const rules = buildCollectionRules('any', [
      { field: 'author', operator: 'equals', value: 'alice' },
    ]);
    expect(rules.match).toBe('any');
    expect(rules.conditions).toHaveLength(1);
  });

  it('presetRules.byAuthor', () => {
    const rules = presetRules.byAuthor('alice');
    expect(rules.conditions[0]).toEqual({
      field: 'author',
      operator: 'equals',
      value: 'alice',
    });
  });

  it('presetRules.byTag', () => {
    const rules = presetRules.byTag('news');
    expect(rules.conditions[0].field).toBe('tag');
    expect(rules.conditions[0].value).toBe('news');
  });

  it('presetRules.highViewCount defaults to 1000', () => {
    const rules = presetRules.highViewCount();
    expect(rules.conditions[0].value).toBe(1000);
  });

  it('presetRules.recentVideos uses gte on date field', () => {
    const rules = presetRules.recentVideos(7);
    expect(rules.conditions[0].field).toBe('date');
    expect(rules.conditions[0].operator).toBe('gte');
  });
});
