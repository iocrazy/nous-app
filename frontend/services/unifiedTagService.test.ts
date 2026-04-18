/**
 * Unit tests for unifiedTagService — covers global tag CRUD, tag
 * groups, statistics, resource associations, and the module-level
 * cache for fetchAllTags.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addResourceTag,
  createTag,
  createTagGroup,
  deleteTag,
  deleteTagGroup,
  fetchAllTags,
  fetchResourceTags,
  fetchTagGroups,
  fetchTagStatistics,
  invalidateAllTagsCache,
  removeResourceTag,
  reorderTagGroups,
  updateTag,
} from './unifiedTagService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ 'Content-Type': 'application/json' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

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
  invalidateAllTagsCache();
});

describe('fetchAllTags', () => {
  it('unwraps tags field when present', async () => {
    stubJson({ tags: [{ id: 't1', name: 'a' }] });
    const rows = await fetchAllTags();
    expect(rows).toHaveLength(1);
  });

  it('falls back to data field', async () => {
    stubJson({ data: [{ id: 't1', name: 'a' }] });
    const rows = await fetchAllTags();
    expect(rows).toHaveLength(1);
  });

  it('caches and returns cached on second call within TTL', async () => {
    const spy = stubJson({ tags: [{ id: 't1' }] });
    await fetchAllTags();
    const callsAfterFirst = spy.mock.calls.length;
    await fetchAllTags();
    expect(spy.mock.calls.length).toBe(callsAfterFirst);
  });

  it('invalidation forces re-fetch', async () => {
    const spy = stubJson({ tags: [] });
    await fetchAllTags();
    invalidateAllTagsCache();
    stubJson({ tags: [{ id: 'new' }] });
    const refreshed = await fetchAllTags();
    expect(refreshed).toHaveLength(1);
    expect(spy.mock.calls.length).toBeGreaterThan(0);
  });

  it('throws on non-ok response', async () => {
    stubJson({ detail: 'forbidden' }, 403);
    await expect(fetchAllTags()).rejects.toThrow(/fetch tags/);
  });
});

describe('tag CRUD', () => {
  it('createTag POSTs with type=user default', async () => {
    const spy = stubJson({ id: 't1', name: 'x' });
    await createTag({ name: 'x' });
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.type).toBe('user');
  });

  it('createTag respects explicit type', async () => {
    const spy = stubJson({ id: 't1' });
    await createTag({ name: 'x', type: 'system' });
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.type).toBe('system');
  });

  it('deleteTag DELETEs /tags/:id', async () => {
    const spy = stubJson({});
    await deleteTag('t1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('updateTag PUTs to /:id', async () => {
    const spy = stubJson({ id: 't1', name: 'new' });
    await updateTag('t1', { name: 'new' });
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
  });

  it('updateTag throws with detail on non-ok', async () => {
    stubJson({ detail: 'conflict' }, 409);
    await expect(updateTag('t1', { name: 'x' })).rejects.toThrow('conflict');
  });
});

describe('tag groups', () => {
  it('fetchTagGroups returns groups array', async () => {
    stubJson({ groups: [{ id: 'g1', name: 'A', sort_order: 0, created_at: '' }] });
    expect(await fetchTagGroups()).toHaveLength(1);
  });

  it('fetchTagGroups returns [] on missing field', async () => {
    stubJson({});
    expect(await fetchTagGroups()).toEqual([]);
  });

  it('createTagGroup POSTs name', async () => {
    const spy = stubJson({ id: 'g1', name: 'A', sort_order: 0, created_at: '' });
    await createTagGroup('A');
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.name).toBe('A');
  });

  it('deleteTagGroup DELETEs /groups/:id', async () => {
    const spy = stubJson({});
    await deleteTagGroup('g1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/tags/groups/g1',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('reorderTagGroups PUTs group_ids', async () => {
    const spy = stubJson({});
    await reorderTagGroups(['g1', 'g2']);
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.group_ids).toEqual(['g1', 'g2']);
  });
});

describe('statistics', () => {
  it('fetchTagStatistics threads limit query', async () => {
    const spy = stubJson({
      success: true,
      top_tags: [],
      total_tagged_videos: 0,
    });
    await fetchTagStatistics(5);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('limit=5');
  });

  it('throws with detail on error', async () => {
    stubJson({ detail: 'nope' }, 500);
    await expect(fetchTagStatistics()).rejects.toThrow('nope');
  });
});

describe('resource tag associations', () => {
  it('fetchResourceTags maps {tag} items', async () => {
    stubJson({ data: [{ tag: { id: 't1', name: 'a' } }] });
    const rows = await fetchResourceTags('r-1');
    expect(rows).toHaveLength(1);
    expect(rows[0].tag.id).toBe('t1');
  });

  it('fetchResourceTags returns [] on missing', async () => {
    stubJson({});
    expect(await fetchResourceTags('r-1')).toEqual([]);
  });

  it('addResourceTag POSTs tag_id body', async () => {
    const spy = stubJson({});
    await addResourceTag('r-1', 't-1');
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.tag_id).toBe('t-1');
  });

  it('removeResourceTag DELETEs nested path', async () => {
    const spy = stubJson({});
    await removeResourceTag('r-1', 't-1');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/resources/r-1/tags/t-1',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});
