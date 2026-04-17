/**
 * Unit tests for the unified API client.
 *
 * Covers the contracts that services rely on:
 *   - Auth header injection (Bearer token / X-API-Key fallback / X-Team-Id)
 *   - URL + query string construction
 *   - JSON body vs raw body handling
 *   - Non-2xx → ApiError with envelope parsing
 *   - 204 returns undefined
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  apiClient,
  apiFetch,
  apiJson,
  buildAuthHeaders,
} from './apiClient';

// The client reads from getApiUrl() and getSupabaseAccessToken(); stub both.
vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

const getToken = vi.fn<() => Promise<string | null>>();
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: () => getToken(),
}));

interface MockResponseInit {
  ok?: boolean;
  status?: number;
  headers?: Headers;
  body?: unknown;
}

function mockFetchOnce(response: MockResponseInit): void {
  const { body, ...rest } = response;
  const serialized =
    body === undefined
      ? ''
      : typeof body === 'string'
        ? body
        : JSON.stringify(body);

  const fake = {
    ok: rest.ok ?? (rest.status ?? 200) < 400,
    status: rest.status ?? 200,
    headers: rest.headers ?? new Headers(),
    text: async () => serialized,
    json: async () =>
      body && typeof body !== 'string' ? body : JSON.parse(serialized || 'null'),
  } as unknown as Response;

  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(fake);
}

describe('buildAuthHeaders', () => {
  beforeEach(() => {
    getToken.mockReset();
    localStorage.clear();
  });

  it('attaches Bearer token when only Supabase session exists', async () => {
    getToken.mockResolvedValueOnce('t0k3n');
    const headers = await buildAuthHeaders();
    expect(headers.Authorization).toBe('Bearer t0k3n');
    expect(headers['X-API-Key']).toBeUndefined();
  });

  it('prefers X-API-Key over Bearer when both available', async () => {
    localStorage.setItem('mediahub_api_key', 'sk_test_abc');
    getToken.mockResolvedValueOnce('t0k3n');
    const headers = await buildAuthHeaders();
    expect(headers['X-API-Key']).toBe('sk_test_abc');
    expect(headers.Authorization).toBeUndefined();
  });

  it('falls back to legacy douyin_api_key storage', async () => {
    localStorage.setItem('douyin_api_key', 'legacy_key');
    const headers = await buildAuthHeaders();
    expect(headers['X-API-Key']).toBe('legacy_key');
  });

  it('attaches X-Team-Id when selected team is set', async () => {
    localStorage.setItem('mediahub_selected_team', 'team-42');
    getToken.mockResolvedValueOnce(null);
    const headers = await buildAuthHeaders();
    expect(headers['X-Team-Id']).toBe('team-42');
  });

  it('merges extra headers', async () => {
    getToken.mockResolvedValueOnce(null);
    const headers = await buildAuthHeaders({ 'X-Client': 'test' });
    expect(headers['X-Client']).toBe('test');
  });

  it('always sets Content-Type: application/json by default', async () => {
    getToken.mockResolvedValueOnce(null);
    const headers = await buildAuthHeaders();
    expect(headers['Content-Type']).toBe('application/json');
  });
});

describe('URL + query construction', () => {
  beforeEach(() => {
    getToken.mockReset();
    getToken.mockResolvedValue(null);
  });

  it('resolves relative paths against getApiUrl()', async () => {
    mockFetchOnce({ body: {} });
    await apiFetch('/api/v1/foo');
    expect(globalThis.fetch).toHaveBeenCalledWith(
      'https://api.test/api/v1/foo',
      expect.anything(),
    );
  });

  it('leaves absolute URLs alone when absolute: true', async () => {
    mockFetchOnce({ body: {} });
    await apiFetch('https://cdn.example/a.json', { absolute: true });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      'https://cdn.example/a.json',
      expect.anything(),
    );
  });

  it('serializes query params and skips null/undefined', async () => {
    mockFetchOnce({ body: {} });
    await apiFetch('/api/v1/list', {
      query: { a: 1, b: 'x', c: undefined, d: null, e: false },
    });
    const url = (globalThis.fetch as any).mock.calls[0][0];
    expect(url).toContain('a=1');
    expect(url).toContain('b=x');
    expect(url).toContain('e=false');
    expect(url).not.toContain('c=');
    expect(url).not.toContain('d=');
  });

  it('appends query to URLs that already have a query string', async () => {
    mockFetchOnce({ body: {} });
    await apiFetch('/api/v1/foo?bar=1', { query: { baz: 2 } });
    const url = (globalThis.fetch as any).mock.calls[0][0];
    expect(url).toBe('https://api.test/api/v1/foo?bar=1&baz=2');
  });
});

describe('request body handling', () => {
  beforeEach(() => {
    getToken.mockReset();
    getToken.mockResolvedValue(null);
  });

  it('stringifies json bodies', async () => {
    mockFetchOnce({ body: {} });
    await apiClient.post('/api/v1/x', { a: 1 });
    const init = (globalThis.fetch as any).mock.calls[0][1];
    expect(init.body).toBe(JSON.stringify({ a: 1 }));
    expect(init.headers['Content-Type']).toBe('application/json');
  });

  it('does not clobber Content-Type for raw bodies (e.g. FormData)', async () => {
    mockFetchOnce({ body: {} });
    const form = new FormData();
    form.append('file', 'x');
    await apiFetch('/api/v1/upload', { method: 'POST', raw: form });
    const init = (globalThis.fetch as any).mock.calls[0][1];
    expect(init.body).toBe(form);
    // Content-Type deleted so the browser can set the boundary
    expect(init.headers['Content-Type']).toBeUndefined();
  });

  it('omits body entirely when neither json nor raw supplied', async () => {
    mockFetchOnce({ body: {} });
    await apiClient.get('/api/v1/x');
    const init = (globalThis.fetch as any).mock.calls[0][1];
    expect(init.body).toBeUndefined();
  });
});

describe('response handling', () => {
  beforeEach(() => {
    getToken.mockReset();
    getToken.mockResolvedValue(null);
  });

  it('parses JSON response body', async () => {
    mockFetchOnce({ body: { ok: true, n: 5 } });
    const data = await apiJson<{ ok: boolean; n: number }>('/api/v1/x');
    expect(data.ok).toBe(true);
    expect(data.n).toBe(5);
  });

  it('returns undefined for 204 No Content', async () => {
    mockFetchOnce({ status: 204, body: undefined, ok: true });
    const data = await apiJson('/api/v1/x');
    expect(data).toBeUndefined();
  });

  it('throws ApiError with envelope error + code + request_id on 4xx', async () => {
    const headers = new Headers({ 'x-request-id': 'req-abc' });
    mockFetchOnce({
      ok: false,
      status: 403,
      headers,
      body: { error: 'Access denied', code: 'permission_denied', request_id: 'req-abc' },
    });

    let caught: ApiError | null = null;
    try {
      await apiJson('/api/v1/x');
    } catch (err) {
      caught = err as ApiError;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught!.status).toBe(403);
    expect(caught!.code).toBe('permission_denied');
    expect(caught!.requestId).toBe('req-abc');
    expect(caught!.message).toBe('Access denied');
  });

  it('falls back to `detail` when response body uses FastAPI default shape', async () => {
    mockFetchOnce({
      ok: false,
      status: 422,
      body: { detail: 'bad input' },
    });
    await expect(apiJson('/api/v1/x')).rejects.toMatchObject({
      status: 422,
      message: 'bad input',
    });
  });

  it('tolerates non-JSON error body', async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      body: '<html>oops</html>',
    });
    await expect(apiJson('/api/v1/x')).rejects.toBeInstanceOf(ApiError);
  });
});
