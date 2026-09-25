/**
 * shotsService — URLs, bodies and the typed-error reader. Mocks sit at the
 * apiClient boundary, so the assertions are about what leaves the browser.
 */
import { describe, expect, it, vi } from 'vitest';

const calls = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('./apiClient', async () => {
  const actual = await vi.importActual<typeof import('./apiClient')>('./apiClient');
  return {
    ...actual,
    apiClient: { get: calls.get, post: calls.post },
  };
});
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { ApiError } from './apiClient';
import {
  backfillShots,
  getResourceFrameUrl,
  getResourceShots,
  indexShots,
  shotErrorCode,
} from './shotsService';

describe('shotsService', () => {
  it('reads the cut list of one resource', async () => {
    calls.get.mockResolvedValue({ resource_id: '9007199254740993', indexed: false, index: null, shots: [] });
    const out = await getResourceShots('9007199254740993');
    expect(calls.get).toHaveBeenCalledWith('/api/v1/resources/9007199254740993/shots');
    expect(out.indexed).toBe(false);
  });

  it('indexes with force=false by default and passes force through', async () => {
    calls.post.mockResolvedValue({ task_id: 'wf-1', workflow_id: 'wf-1', resource_id: '1' });
    await indexShots('1');
    expect(calls.post).toHaveBeenLastCalledWith('/api/v1/ai/analyze/index-shots/1', { force: false });
    await indexShots('1', { force: true });
    expect(calls.post).toHaveBeenLastCalledWith('/api/v1/ai/analyze/index-shots/1', { force: true });
  });

  it('backfills with the given limit and dry_run', async () => {
    calls.post.mockResolvedValue({ success: true, dry_run: true });
    await backfillShots({ limit: 20, dry_run: true });
    expect(calls.post).toHaveBeenLastCalledWith('/api/v1/ai/analyze/backfill-shots', {
      limit: 20,
      dry_run: true,
    });
  });

  it('builds the frame URL with ms rounded and the media token when given', () => {
    expect(getResourceFrameUrl('42', 41000.6)).toBe('https://api.test/api/v1/resources/42/frame?ms=41001');
    expect(getResourceFrameUrl('42', -5, 'mt.1.2.sig')).toBe(
      'https://api.test/api/v1/resources/42/frame?ms=0&token=mt.1.2.sig',
    );
  });

  it('reads the typed code from the production envelope, not the http_ code', () => {
    // Verbatim shape of app/core/exceptions.py: the typed code is in `details`.
    const err = new ApiError('409 Conflict', 409, {
      code: 'http_409',
      details: { code: 'already_indexed', message: 'This video is already indexed…' },
    });
    expect(shotErrorCode(err)).toBe('already_indexed');
    expect(shotErrorCode(new ApiError('x', 500, { code: 'http_500', details: null }))).toBeUndefined();
    expect(shotErrorCode(new Error('boom'))).toBeUndefined();
  });
});
