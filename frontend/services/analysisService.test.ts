/**
 * Unit tests for analysisService — pins the contract with the
 * /api/v1/analysis/* endpoints after the apiClient migration.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  analyzeVideo,
  batchAnalyze,
  getAnalysisStatus,
  getAnalysisStats,
  getSuggestedTags,
  getVideoAnalysis,
} from './analysisService';

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubFetch(body: unknown, status: number = 200): void {
  const serialized = JSON.stringify(body);
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => serialized,
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('analysisService endpoints', () => {
  it('getAnalysisStatus GETs /status/:mediaId', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ media_id: 1, status: 'completed' }),
      json: async () => ({ media_id: 1, status: 'completed' }),
    } as unknown as Response);

    const result = await getAnalysisStatus(1);
    expect(result.status).toBe('completed');
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/analysis/status/1');
    expect((init as RequestInit).method).toBe('GET');
  });

  it('getVideoAnalysis returns the parsed body as-is', async () => {
    stubFetch({
      media_id: 2,
      platform_id: 'abc',
      visual_analysis: 'a dog',
      content_categories: ['pets'],
      detected_objects: [],
      scene_description: null,
      suggested_tags: ['dog'],
      analyzed_at: null,
    });
    const result = await getVideoAnalysis(2);
    expect(result.visual_analysis).toBe('a dog');
    expect(result.content_categories).toEqual(['pets']);
  });

  it('analyzeVideo POSTs with force_reanalyze flag', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ message: 'queued', media_id: 3, status: 'pending' }),
      json: async () => ({ message: 'queued', media_id: 3, status: 'pending' }),
    } as unknown as Response);

    await analyzeVideo(3, true);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ force_reanalyze: true });
  });

  it('batchAnalyze passes arrays and limit through', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          message: 'queued',
          queued_count: 2,
          skipped_count: 0,
          media_ids: [1, 2],
        }),
      json: async () => ({
        message: 'queued',
        queued_count: 2,
        skipped_count: 0,
        media_ids: [1, 2],
      }),
    } as unknown as Response);

    await batchAnalyze([1, 2], 100, false);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      media_ids: [1, 2],
      limit: 100,
      force_reanalyze: false,
    });
  });

  it('getSuggestedTags unwraps suggested_tags[]', async () => {
    stubFetch({ suggested_tags: ['a', 'b', 'c'] });
    const tags = await getSuggestedTags(4);
    expect(tags).toEqual(['a', 'b', 'c']);
  });

  it('getSuggestedTags returns [] when field missing', async () => {
    stubFetch({});
    const tags = await getSuggestedTags(5);
    expect(tags).toEqual([]);
  });

  it('getAnalysisStats GETs /stats', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          total_videos: 10,
          analyzed_count: 5,
          pending_count: 2,
          failed_count: 1,
          tagged_count: 3,
          embedding_count: 0,
        }),
      json: async () => ({
        total_videos: 10,
        analyzed_count: 5,
        pending_count: 2,
        failed_count: 1,
        tagged_count: 3,
        embedding_count: 0,
      }),
    } as unknown as Response);

    const stats = await getAnalysisStats();
    expect(stats.total_videos).toBe(10);
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/analysis/stats');
  });
});
