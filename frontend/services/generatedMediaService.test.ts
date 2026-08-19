/**
 * Unit tests for generatedMediaService.
 * Verifies the endpoint URL construction and data unwrapping.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchEntityGenerations,
  fetchGenerations,
  generatedMediaCoverUrl,
  generatedMediaFileUrl,
} from './generatedMediaService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test' }),
}));

function stubFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('fetchGenerations', () => {
  it('calls the generated-media endpoint and unwraps data', async () => {
    const json = { data: { items: [{ id: '1' }], next_cursor: null } };
    const spy = stubFetch(json);

    const page = await fetchGenerations();

    expect(spy).toHaveBeenCalledOnce();
    const [url] = spy.mock.calls[0] as [string, ...unknown[]];
    expect(url).toContain('/api/v1/generated-media');
    expect(page.items).toHaveLength(1);
    expect(page.next_cursor).toBeNull();
  });

  it('passes cursor and kind as query params', async () => {
    const json = { data: { items: [], next_cursor: null } };
    const spy = stubFetch(json);

    await fetchGenerations('abc123', 'image');

    const [url] = spy.mock.calls[0] as [string, ...unknown[]];
    expect(url).toContain('cursor=abc123');
    expect(url).toContain('kind=image');
  });

  it('throws on non-ok response', async () => {
    stubFetch({}, 500);
    await expect(fetchGenerations()).rejects.toThrow('HTTP 500');
  });

  // Regression #1457: never trust the response shape. A payload missing `items`
  // (or `data` entirely) must normalize to an empty page, not leak `undefined`
  // to callers that read `.items`.
  it.each([
    ['data is an empty array', { data: [] }],
    ['data is null', { data: null }],
    ['data has no items key', { data: { next_cursor: 'x' } }],
    ['data.items is null', { data: { items: null, next_cursor: null } }],
    ['no data key at all', {}],
  ])('normalizes a malformed page (%s) to an empty items array', async (_label, body) => {
    stubFetch(body);
    const page = await fetchGenerations();
    expect(page.items).toEqual([]);
    expect(Array.isArray(page.items)).toBe(true);
  });
});

describe('fetchEntityGenerations', () => {
  it('unwraps a well-formed entity page', async () => {
    const spy = stubFetch({ data: { items: [{ id: '9' }], next_cursor: null } });
    const page = await fetchEntityGenerations('character', '42');
    const [url] = spy.mock.calls[0] as [string, ...unknown[]];
    expect(url).toContain('entity_kind=character');
    expect(url).toContain('entity_id=42');
    expect(page.items).toHaveLength(1);
  });

  // Regression #1457: the entity asset strip crashed on this exact shape.
  it('normalizes a malformed entity page to an empty items array', async () => {
    stubFetch({ data: [] });
    const page = await fetchEntityGenerations('character', '42');
    expect(page.items).toEqual([]);
    expect(page.next_cursor).toBeNull();
  });
});

describe('generatedMediaCoverUrl', () => {
  it('returns the no-auth /cover URL for the given id', () => {
    const url = generatedMediaCoverUrl('5');
    expect(url).toContain('/api/v1/generated-media/5/cover');
  });
});

describe('generatedMediaFileUrl', () => {
  it('returns the auth-gated /file URL for the given id', () => {
    const url = generatedMediaFileUrl('5');
    expect(url).toContain('/api/v1/generated-media/5/file');
  });
});

import {
  deleteGeneration,
  GeneratedMediaError,
  promoteGeneration,
} from './generatedMediaService';

describe('promoteGeneration', () => {
  it('POSTs to the promote endpoint and unwraps data', async () => {
    const json = { data: { promoted_resource_id: '555' } };
    const spy = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(json) });
    vi.stubGlobal('fetch', spy);
    const out = await promoteGeneration('7');
    expect(spy.mock.calls[0][0]).toContain('/api/v1/generated-media/7/promote');
    expect(spy.mock.calls[0][1].method).toBe('POST');
    expect(out.promoted_resource_id).toBe('555');
    vi.unstubAllGlobals();
  });

  // Before 2026-08 this threw a bare `Error('HTTP 403')`, and the only caller
  // rendered it as "Something went wrong" — a message that says nothing.
  it('rejects with a typed reason so the caller can name the failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }));
    const err = await promoteGeneration('7').catch((e) => e);
    expect(err).toBeInstanceOf(GeneratedMediaError);
    expect((err as GeneratedMediaError).reason).toBe('forbidden');
    expect((err as GeneratedMediaError).status).toBe(403);
    vi.unstubAllGlobals();
  });
});

describe('deleteGeneration', () => {
  function stubDelete(impl: unknown) {
    const spy = vi.fn().mockImplementation(() => impl as never);
    vi.stubGlobal('fetch', spy);
    return spy;
  }

  it('sends DELETE to the item endpoint and resolves when the row is gone', async () => {
    const spy = stubDelete(
      Promise.resolve({ ok: true, status: 200, json: async () => ({ data: { deleted: true } }) }),
    );
    await expect(deleteGeneration('7')).resolves.toBeUndefined();
    expect(spy.mock.calls[0][0]).toContain('/api/v1/generated-media/7');
    expect(spy.mock.calls[0][1].method).toBe('DELETE');
    vi.unstubAllGlobals();
  });

  // The backend answers "already deleted" and "not in your scope" with the same
  // 200 + {deleted:false} so a delete cannot probe another workspace. Both land
  // on `not-found`; a caller that ignored the flag would report a false success.
  it('maps a 200 carrying deleted:false onto not-found', async () => {
    stubDelete(
      Promise.resolve({ ok: true, status: 200, json: async () => ({ data: { deleted: false } }) }),
    );
    const err = await deleteGeneration('7').catch((e) => e);
    expect(err).toBeInstanceOf(GeneratedMediaError);
    expect((err as GeneratedMediaError).reason).toBe('not-found');
    vi.unstubAllGlobals();
  });

  it.each([
    [401, 'unauthenticated'],
    [403, 'forbidden'],
    [404, 'not-found'],
    [500, 'server'],
  ])('maps HTTP %i onto the %s reason', async (status, reason) => {
    stubDelete(Promise.resolve({ ok: false, status }));
    const err = await deleteGeneration('7').catch((e) => e);
    expect(err).toBeInstanceOf(GeneratedMediaError);
    expect((err as GeneratedMediaError).reason).toBe(reason);
    expect((err as GeneratedMediaError).status).toBe(status);
    vi.unstubAllGlobals();
  });

  it('maps a transport failure onto the network reason', async () => {
    stubDelete(Promise.reject(new TypeError('Failed to fetch')));
    const err = await deleteGeneration('7').catch((e) => e);
    expect(err).toBeInstanceOf(GeneratedMediaError);
    expect((err as GeneratedMediaError).reason).toBe('network');
    vi.unstubAllGlobals();
  });

  it('treats an unreadable success body as a server failure, not a success', async () => {
    stubDelete(
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError('Unexpected token < in JSON');
        },
      }),
    );
    const err = await deleteGeneration('7').catch((e) => e);
    expect(err).toBeInstanceOf(GeneratedMediaError);
    expect((err as GeneratedMediaError).reason).toBe('server');
    vi.unstubAllGlobals();
  });
});
