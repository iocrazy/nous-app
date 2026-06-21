/**
 * Unit tests for generatedMediaService.
 * Verifies the endpoint URL construction and data unwrapping.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchGenerations } from './generatedMediaService';

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
});
