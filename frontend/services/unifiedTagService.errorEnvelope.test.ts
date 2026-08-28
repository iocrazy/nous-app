/**
 * Tag service errors must carry the backend's reason.
 *
 * The backend answers with a typed envelope — `{success, error, code,
 * request_id, details}` — but this service read `.detail` (FastAPI's bare
 * shape), which is never present, so every message collapsed to a generic
 * fallback. `createTag` never even read the body.
 *
 * Prod 2026-08-27: a duplicate create answered
 * `{"error": "Tag 'MiniMax H3' already exists", "code": "http_409"}`
 * and the dialog said "Failed to create tag".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ 'Content-Type': 'application/json' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { createTag, deleteTag, updateTag } from './unifiedTagService';

const envelope = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue({
    ok: false,
    status,
    json: async () => body,
  } as unknown as Response);

describe('unifiedTagService — typed error envelope', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('createTag surfaces the envelope reason, not a generic string', async () => {
    vi.stubGlobal(
      'fetch',
      envelope(409, {
        success: false,
        error: "Tag 'MiniMax H3' already exists",
        code: 'http_409',
        request_id: 'req-1',
        details: null,
      }),
    );
    await expect(createTag({ name: 'MiniMax H3' })).rejects.toThrow(
      "Tag 'MiniMax H3' already exists",
    );
  });

  it('updateTag surfaces it too', async () => {
    vi.stubGlobal(
      'fetch',
      envelope(422, { success: false, error: 'must be a numeric tag group id' }),
    );
    await expect(updateTag('1', { group_id: 'nope' })).rejects.toThrow(
      'must be a numeric tag group id',
    );
  });

  it('still reads a plain FastAPI {detail} body', async () => {
    vi.stubGlobal('fetch', envelope(403, { detail: 'Access denied' }));
    await expect(deleteTag('1')).rejects.toThrow('Access denied');
  });

  it('falls back to a labelled status when the body carries no reason', async () => {
    vi.stubGlobal('fetch', envelope(500, {}));
    await expect(createTag({ name: 'x' })).rejects.toThrow(
      'Failed to create tag (HTTP 500)',
    );
  });

  it('survives a non-JSON body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError('Unexpected token <');
        },
      } as unknown as Response),
    );
    await expect(createTag({ name: 'x' })).rejects.toThrow(
      'Failed to create tag (HTTP 502)',
    );
  });
});
