/**
 * Unit tests for reviewCommentsService — share-code URL shapes and
 * default timecode/visibility handling.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createShareComment,
  fetchShareComments,
} from './reviewCommentsService';

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

describe('reviewCommentsService', () => {
  it('fetchShareComments unwraps data array', async () => {
    stubJson({ data: [{ id: 'c1', content: 'hi' }] });
    const rows = await fetchShareComments('SHARE1');
    expect(rows).toHaveLength(1);
  });

  it('fetchShareComments returns [] on missing data', async () => {
    stubJson({});
    expect(await fetchShareComments('SHARE1')).toEqual([]);
  });

  it('createShareComment POSTs with defaults for timecode + visibility', async () => {
    const spy = stubJson({ data: { id: 'c1', content: 'hi' } });
    await createShareComment('SHARE1', { content: 'hi' });
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.content).toBe('hi');
    expect(body.timecode).toBeNull();
    expect(body.visibility).toBe('all');
  });

  it('createShareComment passes explicit timecode/visibility', async () => {
    const spy = stubJson({ data: { id: 'c1', content: 'hi' } });
    await createShareComment('SHARE1', {
      content: 'hi',
      timecode: 42,
      visibility: 'private',
    });
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.timecode).toBe(42);
    expect(body.visibility).toBe('private');
  });

  it('createShareComment throws on empty data', async () => {
    stubJson({});
    await expect(
      createShareComment('SHARE1', { content: 'hi' }),
    ).rejects.toThrow(/Empty/);
  });
});
