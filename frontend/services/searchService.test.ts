/**
 * Unit tests for searchService.
 *
 * localSearch is pure and tested exhaustively because it runs on every
 * keystroke; the network-backed helpers are covered by URL/method/body
 * shape assertions.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  activateVectorSpace,
  createVectorSpace,
  deleteVectorSpace,
  findSimilarVideos,
  getVectorSpaceCatalog,
  hybridSearch,
  localSearch,
  semanticSearch,
  textSearch,
} from './searchService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubResponse(body: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('semanticSearch', () => {
  it('POSTs query + limit + threshold', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          results: [],
          total: 0,
          query: 'q',
          search_type: 'semantic',
          processing_time_ms: 5,
        }),
      json: async () => ({
        results: [],
        total: 0,
        query: 'q',
        search_type: 'semantic',
        processing_time_ms: 5,
      }),
    } as unknown as Response);

    await semanticSearch('cats', 50, 0.4);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      query: 'cats',
      limit: 50,
      threshold: 0.4,
    });
  });
});

describe('hybridSearch', () => {
  it('merges filters into the JSON body', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          results: [],
          total: 0,
          query: 'q',
          search_type: 'hybrid',
          processing_time_ms: 5,
        }),
      json: async () => ({
        results: [],
        total: 0,
        query: 'q',
        search_type: 'hybrid',
        processing_time_ms: 5,
      }),
    } as unknown as Response);

    await hybridSearch('q', {
      tag_ids: [1, 2],
      author: 'Alice',
      min_views: 100,
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.tag_ids).toEqual([1, 2]);
    expect(body.author).toBe('Alice');
    expect(body.min_views).toBe(100);
  });

  it('forwards the search scope so Smart Search honours the checkboxes', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ results: [], total: 0, query: 'q', search_type: 'hybrid' }),
      json: async () => ({ results: [], total: 0, query: 'q', search_type: 'hybrid' }),
    } as unknown as Response);

    await hybridSearch('q', {}, 100, 0.5, ['title', 'tags']);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.fields).toEqual(['title', 'tags']);
  });

  it('omits fields entirely when no scope is given, letting the backend default apply', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ results: [], total: 0, query: 'q', search_type: 'hybrid' }),
      json: async () => ({ results: [], total: 0, query: 'q', search_type: 'hybrid' }),
    } as unknown as Response);

    await hybridSearch('q');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect('fields' in body).toBe(false);
  });
});

describe('textSearch', () => {
  // This is the DEFAULT search mode — both the Enter-key path and the
  // 300ms quick-search debounce in DownloadsView go through here, so it is
  // the request that actually carries the widened default scope (tags /
  // notes) to the backend. It had no coverage at all.
  //
  // Response bodies below use the real wire shape: the endpoint returns
  // ``videos`` (full parsed_media rows) alongside ``results``.
  const stubTextResponse = () =>
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          results: [],
          videos: [],
          total: 0,
          query: 'q',
          search_type: 'text',
        }),
      json: async () => ({
        results: [],
        videos: [],
        total: 0,
        query: 'q',
        search_type: 'text',
      }),
    } as unknown as Response);

  it('forwards the scope so the picked checkboxes reach the RPC', async () => {
    const spy = stubTextResponse();

    await textSearch('krea', 1000, ['title', 'tags', 'notes']);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/api/v1/search/text');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.fields).toEqual(['title', 'tags', 'notes']);
    expect(body.limit).toBe(1000);
  });

  it('carries the transcript scope through untouched', async () => {
    // The scope whose backend half moved to resource_transcripts in 463.
    // If the client dropped it the migration would look broken from the UI.
    const spy = stubTextResponse();

    await textSearch('krea', 1000, ['transcript']);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.fields).toEqual(['transcript']);
  });

  it('omits fields when the scope is absent or empty so the backend default applies', async () => {
    const spy = stubTextResponse();
    await textSearch('krea');
    expect(
      'fields' in JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string),
    ).toBe(false);

    const spy2 = stubTextResponse();
    await textSearch('krea', 1000, []);
    expect(
      'fields' in JSON.parse((spy2.mock.calls[0][1] as RequestInit).body as string),
    ).toBe(false);
  });
});

describe('findSimilarVideos', () => {
  it('uses GET /similar/:id with query params', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          results: [],
          total: 0,
          query: '',
          search_type: 'similar',
          processing_time_ms: 0,
        }),
      json: async () => ({
        results: [],
        total: 0,
        query: '',
        search_type: 'similar',
        processing_time_ms: 0,
      }),
    } as unknown as Response);

    await findSimilarVideos(42, 5, 0.8);
    const [url, init] = spy.mock.calls[0];
    expect((init as RequestInit).method).toBe('GET');
    expect(url).toContain('/api/v1/search/similar/42');
    expect(url).toContain('limit=5');
    expect(url).toContain('threshold=0.8');
  });
});

describe('localSearch (pure)', () => {
  const library = [
    {
      platform_id: 'a',
      title: 'Cat videos are best',
      description: 'cute',
      author: 'Alice',
      tags: ['cats', 'funny'],
    },
    {
      platform_id: 'b',
      title: 'Puppy montage',
      description: 'doggos',
      author: 'Bob',
      tags: ['dogs'],
    },
    {
      platform_id: 'c',
      title: '北京美食',
      description: '早餐 午餐 晚餐',
      author: 'Carol',
      tags: [],
    },
  ];

  it('returns empty response for blank query', () => {
    const result = localSearch('', library);
    expect(result.results).toHaveLength(0);
    expect(result.total).toBe(0);
  });

  it('matches on title', () => {
    const result = localSearch('cat', library);
    expect(result.results.map(r => r.platform_id)).toContain('a');
  });

  it('matches on tag', () => {
    const result = localSearch('dogs', library);
    expect(result.results.map(r => r.platform_id)).toContain('b');
  });

  it('matches on author case-insensitively', () => {
    const result = localSearch('ALICE', library);
    expect(result.results.map(r => r.platform_id)).toContain('a');
  });

  it('matches CJK text after removing spaces', () => {
    // Query "美 食" (with space) must still match "美食"
    const result = localSearch('美 食', library);
    expect(result.results.map(r => r.platform_id)).toContain('c');
  });

  it('respects the limit parameter', () => {
    const result = localSearch('a', library, 1);
    expect(result.results).toHaveLength(1);
  });

  it('reports local search_type', () => {
    const result = localSearch('cat', library);
    expect(result.search_type).toBe('local');
  });
});

describe('vector space switching', () => {
  // Snowflake past 2^53: must travel as the exact string, never a number.
  const SPACE_ID = '1234567890123456789';

  it('createVectorSpace POSTs the catalog model name', async () => {
    stubResponse({ id: SPACE_ID, actual_model: 'wemm-embedding-2b', protocol: 'openai-embeddings-chat', dims: 2048, modalities: ['text'], instruction_version: 'en_keyword_v1' });
    const spy = vi.mocked(globalThis.fetch);
    const out = await createVectorSpace('nous-wemm-embedding-2b');
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://api.test/api/v1/search/vectors/spaces');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ model_name: 'nous-wemm-embedding-2b' });
    expect(out.id).toBe(SPACE_ID);
  });

  it('activateVectorSpace POSTs to the space id path', async () => {
    stubResponse({ status: 'ok', space: null, layers: [], spaces: [], can_manage: true });
    const spy = vi.mocked(globalThis.fetch);
    await activateVectorSpace(SPACE_ID);
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.test/api/v1/search/vectors/spaces/${SPACE_ID}/activate`);
    expect(init.method).toBe('POST');
  });

  it('deleteVectorSpace DELETEs the space id path', async () => {
    stubResponse({ deleted: true, space_id: SPACE_ID, deleted_vectors: 187 });
    const spy = vi.mocked(globalThis.fetch);
    const out = await deleteVectorSpace(SPACE_ID);
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.test/api/v1/search/vectors/spaces/${SPACE_ID}`);
    expect(init.method).toBe('DELETE');
    expect(out.deleted_vectors).toBe(187);
  });

  it('getVectorSpaceCatalog GETs the platform embedding rows', async () => {
    stubResponse({ models: [{ name: 'nous-wemm-embedding-2b', display_name: 'WeMM 2B', type: 'embedding' }] });
    const spy = vi.mocked(globalThis.fetch);
    const out = await getVectorSpaceCatalog();
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://api.test/api/v1/search/vectors/catalog');
    expect(init.method).toBe('GET');
    expect(out.map((m) => m.name)).toEqual(['nous-wemm-embedding-2b']);
  });
});
