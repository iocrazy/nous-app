/**
 * Unit tests for libraryService — pins URL/method/body shapes and
 * envelope unwrap behavior.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createLibrary,
  deleteLibrary,
  fetchLibraries,
  updateLibrary,
} from './libraryService';

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

describe('libraryService', () => {
  it('fetchLibraries threads scope_id and unwraps data', async () => {
    const spy = stubJson({ data: [{ id: 'l1' }, { id: 'l2' }] });
    const rows = await fetchLibraries('t-1');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('scope_id=t-1');
    expect(rows).toHaveLength(2);
  });

  it('fetchLibraries returns [] when data missing', async () => {
    stubJson({});
    expect(await fetchLibraries('t-1')).toEqual([]);
  });

  it('createLibrary POSTs with scope_type=team', async () => {
    const spy = stubJson({ data: { id: 'l1', name: 'x' } });
    await createLibrary({ name: 'x', scope_id: 't-1' });
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.scope_type).toBe('team');
    expect(body.name).toBe('x');
  });

  it('createLibrary throws on empty data', async () => {
    stubJson({});
    await expect(createLibrary({ name: 'x', scope_id: 't-1' })).rejects.toThrow(
      /Empty/,
    );
  });

  it('updateLibrary PATCHes /:id', async () => {
    const spy = stubJson({ data: { id: 'l1', name: 'new' } });
    await updateLibrary('l1', { name: 'new' });
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PATCH');
  });

  it('deleteLibrary DELETEs /:id', async () => {
    const spy = stubJson({});
    await deleteLibrary('l1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});
