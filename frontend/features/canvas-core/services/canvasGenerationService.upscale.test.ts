// features/canvas-core/services/canvasGenerationService.upscale.test.ts
//
// upscaleGeneration through the REAL apiClient (only `fetch` is stubbed), so
// the production ErrorResponse envelope is parsed by the same toApiError the
// app uses. Bodies are the verbatim envelope app/core/exceptions.py emits for
// the typed 5xx codes (TYPED_5XX_CODES): `error` is "Request failed", `code` is
// http_<status>, and the typed payload sits under `details`.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../utils/apiConfig', () => ({
  getApiUrl: () => 'http://api.test',
  getFallbackApiUrl: () => null,
  isCallerAbort: () => false,
  reportApiNetworkFailure: () => undefined,
}));
vi.mock('../../../supabaseClient', () => ({
  getSupabaseAccessToken: async () => 't',
}));

const { upscaleGeneration } = await import('./canvasGenerationService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

describe('upscaleGeneration', () => {
  it('returns the new generation on success', async () => {
    fetchMock.mockResolvedValueOnce(
      json(200, { data: { id: '991', url: '/api/v1/generated-media/991/cover' } }),
    );
    await expect(upscaleGeneration('7', '2k')).resolves.toEqual({
      id: '991',
      url: '/api/v1/generated-media/991/cover',
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/generated-media/7/upscale');
    expect(JSON.parse(init.body)).toEqual({ resolution: '2k' });
  });

  it('names provider + upstream code from the production 502 envelope', async () => {
    fetchMock.mockResolvedValueOnce(
      json(502, {
        success: false,
        error: 'Request failed',
        code: 'http_502',
        request_id: 'req-1',
        details: {
          code: 'upscale_backend_failed',
          provider: 'nous-studio-upscale',
          upstream_status: 404,
          upstream_code: 'model_not_found',
        },
      }),
    );
    await expect(upscaleGeneration('7')).rejects.toThrow(
      'nous-studio-upscale: model_not_found',
    );
  });

  it('falls back to the upstream HTTP status when there is no upstream code', async () => {
    fetchMock.mockResolvedValueOnce(
      json(502, {
        success: false,
        error: 'Request failed',
        code: 'http_502',
        request_id: 'req-2',
        details: {
          code: 'upscale_backend_failed',
          provider: 'jimeng-cli-image',
          upstream_status: null,
          upstream_code: null,
        },
      }),
    );
    await expect(upscaleGeneration('7')).rejects.toThrow('jimeng-cli-image: HTTP 502');
  });

  it('says no backend is enabled on the production 503 envelope', async () => {
    fetchMock.mockResolvedValueOnce(
      json(503, {
        success: false,
        error: 'Request failed',
        code: 'http_503',
        request_id: 'req-3',
        details: { code: 'upscale_unavailable', reason: 'no_backend' },
      }),
    );
    await expect(upscaleGeneration('7')).rejects.toThrow('no upscale backend enabled');
  });

  it('keeps the original error for an untyped refusal', async () => {
    fetchMock.mockResolvedValueOnce(
      json(404, {
        success: false,
        error: 'generation not found',
        code: 'http_404',
        request_id: 'req-4',
        details: null,
      }),
    );
    await expect(upscaleGeneration('7')).rejects.toThrow('generation not found');
  });
});
