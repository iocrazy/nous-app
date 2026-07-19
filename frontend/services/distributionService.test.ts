import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock the HTTP boundary, NOT the service. `listLibraryVideos` goes through
// `request()` → `fetch`, so stubbing `fetch` exercises the real row-shape
// mapping. The auth/url helpers are mocked only to stay offline + give the
// cover URL a stable base to assert against.
vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test' }),
}));
vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

import { listLibraryVideos, listLibraryMedia } from './distributionService';

/**
 * The real `GET /api/v1/resources` row (backend `get_resource_items`):
 * a `resource_items` join row whose top-level `id` is the JOIN row id, with
 * the true resource id in `resource_id` and file fields nested under
 * `resource` (`row_to_json(r.*)`). This is the exact shape the picker gets in
 * prod — pinning it here is the regression guard against re-reading `filename`
 * / `thumbnail_path` / `id` off the top level.
 */
const REAL_ROW = {
  id: '5555555555555555555', // resource_items row id — must NOT be used as the video id
  resource_id: '9999999999999999999', // the real resources.id
  scope_id: '111',
  folder_id: '222',
  created_at: '2026-07-18T00:00:00Z',
  resource: {
    filename: 'launch-clip.mp4',
    thumbnail_path: 'covers/2026/07/abc.jpg',
    mime_type: 'video/mp4',
  },
};

function mockFetchJson(body: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response);
}

describe('listLibraryVideos — real nested row shape', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetchJson({ success: true, data: [REAL_ROW] }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('maps id from resource_id (not the join-row id)', async () => {
    const videos = await listLibraryVideos('scope-1');
    expect(videos).toHaveLength(1);
    // The published resource_ids MUST be the real resource id, else
    // createPublishTask sends join-row ids and get_resource_media_url 404s.
    expect(videos[0].id).toBe('9999999999999999999');
    expect(videos[0].id).not.toBe(REAL_ROW.id);
  });

  it('reads filename from the nested resource object', async () => {
    const videos = await listLibraryVideos('scope-1');
    expect(videos[0].filename).toBe('launch-clip.mp4');
  });

  it('builds the cover URL from the nested thumbnail_path + resource id', async () => {
    const videos = await listLibraryVideos('scope-1');
    expect(videos[0].thumbnail_url).toBe(
      'https://api.test/api/v1/resources/9999999999999999999/cover',
    );
  });

  it('falls back to Untitled + null cover when the nested resource is empty', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchJson({
        success: true,
        data: [{ id: 'a', resource_id: 'b', resource: {} }],
      }),
    );
    const videos = await listLibraryVideos('scope-1');
    expect(videos[0]).toEqual({
      id: 'b', filename: 'Untitled', thumbnail_url: null,
      mime_type: null, gallery_count: undefined,
    });
  });

  it('falls back to the join-row id when resource_id is absent (defensive)', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchJson({
        success: true,
        data: [{ id: 'only-join-id', resource: { filename: 'x.mp4' } }],
      }),
    );
    const videos = await listLibraryVideos('scope-1');
    expect(videos[0].id).toBe('only-join-id');
  });

  it('forwards the tag filter and fails soft to [] on HTTP error', async () => {
    const errored = mockFetchJson({}, false, 500);
    vi.stubGlobal('fetch', errored);
    const consoleErr = vi.spyOn(console, 'error').mockImplementation(() => {});
    const videos = await listLibraryVideos('scope-1', { tagId: 'tag-42' });
    expect(videos).toEqual([]);
    // The request carried the tag filter into the query string.
    const calledUrl = errored.mock.calls[0][0] as string;
    expect(calledUrl).toContain('tag_ids=tag-42');
    consoleErr.mockRestore();
  });
});

describe('listLibraryMedia — gallery entities in images mode', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('requests both image and gallery types in images mode', async () => {
    const spy = mockFetchJson({ success: true, data: [] });
    vi.stubGlobal('fetch', spy);

    await listLibraryVideos('scope-1', { mediaType: 'image' });

    const url = spy.mock.calls[0][0] as string;
    // FastAPI reads repeated keys as a List → image AND gallery both requested.
    expect(url).toContain('types=image');
    expect(url).toContain('types=gallery');
  });

  it('requests only the video type in video mode (no gallery)', async () => {
    const spy = mockFetchJson({ success: true, data: [] });
    vi.stubGlobal('fetch', spy);

    await listLibraryVideos('scope-1', { mediaType: 'video' });

    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('types=video');
    expect(url).not.toContain('types=gallery');
  });

  it('passes the gallery mime + child count through to the row', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchJson({
        success: true,
        data: [{
          id: 'j1', resource_id: 'gal-1',
          resource: {
            filename: 'trip', thumbnail_path: 'x.jpg',
            mime_type: 'application/x-mediahub-gallery', gallery_count: 4,
          },
        }],
      }),
    );
    const rows = await listLibraryVideos('scope-1', { mediaType: 'image' });
    expect(rows[0].mime_type).toBe('application/x-mediahub-gallery');
    expect(rows[0].gallery_count).toBe(4);
  });
});

describe('listLibraryVideos — own-content provenance filter', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('requests only own content (upload/generated/derived), never web downloads', async () => {
    const spy = mockFetchJson({ success: true, data: [] });
    vi.stubGlobal('fetch', spy);

    await listLibraryVideos('scope-1');

    // FastAPI reads repeated keys as a List → one `source_types=` per value.
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('source_types=upload');
    expect(url).toContain('source_types=generated');
    expect(url).toContain('source_types=derived');
    // Platform-downloaded material must never be requested.
    expect(url).not.toContain('source_types=web');
    // The scope-wide + video-typed query is preserved.
    expect(url).toContain('all_folders=true');
    expect(url).toContain('types=video');
    expect(url).toContain('scope_id=scope-1');
  });

  it('keeps the own-content filter alongside a tag filter', async () => {
    const spy = mockFetchJson({ success: true, data: [] });
    vi.stubGlobal('fetch', spy);

    await listLibraryVideos('scope-1', { tagId: 'tag-9' });

    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('source_types=upload');
    expect(url).toContain('source_types=generated');
    expect(url).toContain('source_types=derived');
    expect(url).toContain('tag_ids=tag-9');
  });
});

describe('listLibraryMedia — media type filter', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('defaults to types=video (back-compat alias)', async () => {
    const spy = mockFetchJson({ success: true, data: [] });
    vi.stubGlobal('fetch', spy);
    await listLibraryMedia('scope-1');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('types=video');
  });

  it('requests types=image when mediaType is image', async () => {
    const spy = mockFetchJson({ success: true, data: [] });
    vi.stubGlobal('fetch', spy);
    await listLibraryMedia('scope-1', { mediaType: 'image' });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('types=image');
    expect(url).not.toContain('types=video');
    // own-content provenance filter still applies to images.
    expect(url).toContain('source_types=upload');
  });

  it('listLibraryVideos is the video-defaulted alias of listLibraryMedia', () => {
    expect(listLibraryVideos).toBe(listLibraryMedia);
  });
});
