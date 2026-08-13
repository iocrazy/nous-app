/**
 * translateGenPrompt 的失败面。
 *
 * provider 故障（429/超时/凭证）由 `backend/app/core/provider_errors.py` 的
 * handler 接管，响应体是 `ErrorResponse` envelope —— 下面的桩照抄它的真实字段
 * （`success` / `error` / `code` / `request_id` / `details`），**不是** FastAPI
 * 裸 HTTPException 的 `detail`（「边界 mock 必须用真实 JSON 形状」）。读错字段
 * 的后果不是报错而是静默降级成通用文案，正是这两条测试要钉住的东西。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { translateGenPrompt } from './resourceService';

vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn().mockResolvedValue({}) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

describe('translateGenPrompt — typed failure surface', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('carries the ErrorResponse message and code out of a provider 503', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({
          success: false,
          error: 'The model provider is rate-limiting requests. Try again shortly.',
          code: 'provider_rate_limit',
          request_id: 'req-abc',
          details: null,
        }),
      }),
    );

    const err = await translateGenPrompt('r1', 'zh').catch((e) => e);

    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe(
      'The model provider is rate-limiting requests. Try again shortly.',
    );
    expect(err.code).toBe('provider_rate_limit');
  });

  it('falls back to a generic message when the body carries no envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new SyntaxError('Unexpected end of JSON input');
        },
      }),
    );

    const err = await translateGenPrompt('r1', 'en').catch((e) => e);

    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe('Failed to translate prompt');
    expect(err.code).toBeUndefined();
  });
});
