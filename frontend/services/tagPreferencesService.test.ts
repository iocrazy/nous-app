/**
 * Unit tests for tagPreferencesService — module-level cache, default
 * fallback on error, and invalidation + update flow.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchTagPreferences,
  invalidatePreferencesCache,
  updateTagPreferences,
} from './tagPreferencesService';

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
  invalidatePreferencesCache();
});

describe('fetchTagPreferences', () => {
  const sample = {
    starred_tag_ids: ['t1'],
    picker_settings: {
      layout: 'grid' as const,
      columnWidth: 'large' as const,
      showStarred: false,
      showRecently: true,
      showRecommended: true,
      showCount: false,
    },
    panel_size: { width: 600, height: 500 },
  };

  it('returns cached value on second call', async () => {
    stubJson(sample);
    const first = await fetchTagPreferences();
    expect(first.starred_tag_ids).toEqual(['t1']);

    const second = await fetchTagPreferences();
    expect(second.panel_size.width).toBe(600);
  });

  it('falls back to defaults on API error', async () => {
    stubJson({ detail: 'oops' }, 500);
    const result = await fetchTagPreferences();
    expect(result.starred_tag_ids).toEqual([]);
    expect(result.picker_settings.layout).toBe('list');
    expect(result.panel_size.width).toBe(480);
  });
});

describe('updateTagPreferences', () => {
  it('PATCHes /tags/preferences and refreshes cache', async () => {
    const updated = {
      starred_tag_ids: ['t2'],
      picker_settings: {
        layout: 'list' as const,
        columnWidth: 'small' as const,
        showStarred: true,
        showRecently: true,
        showRecommended: false,
        showCount: true,
      },
      panel_size: { width: 480, height: 400 },
    };
    const spy = stubJson(updated);
    const result = await updateTagPreferences({ starred_tag_ids: ['t2'] });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PATCH');
    const body = JSON.parse(init.body as string);
    expect(body.starred_tag_ids).toEqual(['t2']);
    expect(result.starred_tag_ids).toEqual(['t2']);
  });
});

describe('invalidatePreferencesCache', () => {
  it('forces next fetch to hit the network again', async () => {
    const defaults = {
      picker_settings: {
        layout: 'list' as const,
        columnWidth: 'medium' as const,
        showStarred: true,
        showRecently: true,
        showRecommended: false,
        showCount: true,
      },
      panel_size: { width: 480, height: 400 },
    };
    const spy = stubJson({ ...defaults, starred_tag_ids: [] });
    await fetchTagPreferences();
    const callsAfterFirst = spy.mock.calls.length;

    invalidatePreferencesCache();

    stubJson({ ...defaults, starred_tag_ids: ['new'] });
    const refreshed = await fetchTagPreferences();
    expect(spy.mock.calls.length).toBeGreaterThan(callsAfterFirst);
    expect(refreshed.starred_tag_ids).toEqual(['new']);
  });
});
