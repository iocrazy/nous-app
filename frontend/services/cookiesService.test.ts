/**
 * Unit tests for cookiesService — pins URL/method shapes for cookies +
 * custom headers endpoints.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  deleteCookie,
  fetchCookieStatuses,
  fetchHeaders,
  setCookie,
  setHeaders,
} from './cookiesService';

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

describe('cookiesService', () => {
  it('fetchCookieStatuses unwraps cookies array', async () => {
    stubJson({
      cookies: [
        {
          platform: 'douyin',
          has_cookie: true,
          is_valid: true,
          error_message: null,
          updated_at: '2026-04-17',
        },
      ],
    });
    const rows = await fetchCookieStatuses();
    expect(rows).toHaveLength(1);
    expect(rows[0].platform).toBe('douyin');
  });

  it('fetchCookieStatuses returns [] when cookies field missing', async () => {
    stubJson({});
    expect(await fetchCookieStatuses()).toEqual([]);
  });

  it('setCookie PUTs to /settings/cookies/:platform', async () => {
    const spy = stubJson({});
    await setCookie('douyin', 'sessionid=abc');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/settings/cookies/douyin',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.cookie_text).toBe('sessionid=abc');
    expect(body.cookie_file).toBeNull();
  });

  it('setCookie sends nulls when no args provided', async () => {
    const spy = stubJson({});
    await setCookie('douyin');
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.cookie_text).toBeNull();
    expect(body.cookie_file).toBeNull();
  });

  it('deleteCookie DELETEs /settings/cookies/:platform', async () => {
    const spy = stubJson({});
    await deleteCookie('bilibili');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });

  it('fetchHeaders GETs /settings/headers/:platform', async () => {
    const spy = stubJson({ platform: 'douyin', headers_text: 'ua: x' });
    const result = await fetchHeaders('douyin');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/settings/headers/douyin',
    );
    expect(result.headers_text).toBe('ua: x');
  });

  it('setHeaders PUTs headers body to /settings/headers/:platform', async () => {
    const spy = stubJson({});
    await setHeaders('douyin', 'User-Agent: foo');
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.headers_text).toBe('User-Agent: foo');
  });
});
