/**
 * Unit tests for cleanupService — URL/body shapes plus formatBytes/
 * reason-label helpers.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  batchCleanupAction,
  formatBytes,
  getCleanupData,
  getCleanupStats,
  getCleanupSuggestions,
  getReasonColor,
  getReasonLabel,
  getStorageBreakdown,
  markKeepForever,
  takeCleanupAction,
  unmarkKeepForever,
} from './cleanupService';

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

describe('cleanupService API', () => {
  it('getCleanupData threads limit + include_duplicates', async () => {
    const spy = stubJson({ suggestions: [], total_count: 0 });
    await getCleanupData(100, false);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('limit=100');
    expect(url).toContain('include_duplicates=false');
  });

  it('getCleanupSuggestions defaults include_duplicates=true', async () => {
    const spy = stubJson({ suggestions: [] });
    await getCleanupSuggestions();
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('limit=50');
    expect(url).toContain('include_duplicates=true');
  });

  it('getCleanupStats hits /stats', async () => {
    const spy = stubJson({ total_videos: 0 });
    await getCleanupStats();
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/cleanup/stats');
  });

  it('getStorageBreakdown hits /storage', async () => {
    const spy = stubJson({ total_bytes: 0 });
    await getStorageBreakdown();
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/cleanup/storage',
    );
  });

  it('takeCleanupAction POSTs to videos/:id/action with action body', async () => {
    const spy = stubJson({ message: 'ok', media_id: 42 });
    await takeCleanupAction(42, 'delete');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/cleanup/videos/42/action',
    );
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.action).toBe('delete');
  });

  it('batchCleanupAction POSTs media_ids + action', async () => {
    const spy = stubJson({
      message: 'ok',
      action: 'keep_forever',
      success_count: 2,
      failed_count: 0,
      failed_ids: [],
    });
    await batchCleanupAction([1, 2], 'keep_forever');
    const body = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.media_ids).toEqual([1, 2]);
    expect(body.action).toBe('keep_forever');
  });

  it('markKeepForever POSTs /:id/keep', async () => {
    const spy = stubJson({ message: 'ok', media_id: 7 });
    await markKeepForever(7);
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/cleanup/videos/7/keep',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('unmarkKeepForever DELETEs /:id/keep', async () => {
    const spy = stubJson({ message: 'ok', media_id: 7 });
    await unmarkKeepForever(7);
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});

describe('cleanupService formatters', () => {
  it('formatBytes returns 0 B for zero', () => {
    expect(formatBytes(0)).toBe('0 B');
  });

  it('formatBytes scales KB/MB/GB', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1024 * 1024)).toBe('1 MB');
    expect(formatBytes(1024 * 1024 * 1024)).toBe('1 GB');
  });

  it('formatBytes keeps two decimals for fractional', () => {
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  it('getReasonLabel maps known reasons', () => {
    expect(getReasonLabel('never_viewed')).toBe('Never Viewed');
    expect(getReasonLabel('duplicate_content')).toBe('Potential Duplicate');
    expect(getReasonLabel('old_unused')).toBe('Not Recently Viewed');
    expect(getReasonLabel('large_file')).toBe('Large File');
  });

  it('getReasonColor returns tailwind class per reason', () => {
    expect(getReasonColor('never_viewed')).toContain('yellow');
    expect(getReasonColor('duplicate_content')).toContain('purple');
    expect(getReasonColor('large_file')).toContain('red');
  });
});
