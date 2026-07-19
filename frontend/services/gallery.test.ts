import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock only the HTTP boundary + auth/url helpers so the real request-building
// (URL params, method, body shape) is exercised.
vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test' }),
}));
vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

import {
  createGallery,
  getGalleryItems,
  setGalleryItems,
} from './resourceService';

interface FetchCall {
  url: string;
  init: RequestInit;
}

function trackingFetch(body: unknown, ok = true, status = 200) {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return {
      ok,
      status,
      json: () => Promise.resolve(body),
    } as unknown as Response;
  });
  return { fn, calls };
}

describe('gallery service — HTTP shapes', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('createGallery POSTs to /resources/galleries with scope + filename query', async () => {
    const { fn, calls } = trackingFetch({ success: true, data: { id: '77' } });
    vi.stubGlobal('fetch', fn);

    const out = await createGallery('scope-1', 'My Trip', 'folder-9');
    expect(out).toEqual({ id: '77' });

    const { url, init } = calls[0];
    expect(init.method).toBe('POST');
    expect(url).toContain('/api/v1/resources/galleries?');
    expect(url).toContain('scope_id=scope-1');
    expect(url).toContain('filename=My+Trip');
    expect(url).toContain('folder_id=folder-9');
  });

  it('createGallery omits folder_id when not given', async () => {
    const { fn, calls } = trackingFetch({ success: true, data: { id: '77' } });
    vi.stubGlobal('fetch', fn);
    await createGallery('scope-1', 'No Folder');
    expect(calls[0].url).not.toContain('folder_id');
  });

  it('setGalleryItems PUTs the ordered image_ids body with scope query', async () => {
    const { fn, calls } = trackingFetch({ success: true, data: [] });
    vi.stubGlobal('fetch', fn);

    await setGalleryItems('77', 'scope-1', ['10', '20', '30']);
    const { url, init } = calls[0];
    expect(init.method).toBe('PUT');
    expect(url).toContain('/api/v1/resources/77/gallery-items?');
    expect(url).toContain('scope_id=scope-1');
    expect(JSON.parse(init.body as string)).toEqual({
      image_ids: ['10', '20', '30'],
    });
  });

  it('getGalleryItems GETs the ordered children', async () => {
    const rows = [
      { id: '10', filename: 'a.jpg', thumbnail_path: 't/a', position: 0 },
      { id: '20', filename: 'b.jpg', thumbnail_path: 't/b', position: 1 },
    ];
    const { fn, calls } = trackingFetch({ success: true, data: rows });
    vi.stubGlobal('fetch', fn);

    const out = await getGalleryItems('77');
    expect(calls[0].url).toBe('https://api.test/api/v1/resources/77/gallery-items');
    expect(out).toHaveLength(2);
    expect(out[0].filename).toBe('a.jpg');
    expect(out[1].position).toBe(1);
  });

  it('throws on non-ok responses', async () => {
    const { fn } = trackingFetch({}, false, 500);
    vi.stubGlobal('fetch', fn);
    await expect(createGallery('s', 'x')).rejects.toThrow();
    await expect(setGalleryItems('1', 's', [])).rejects.toThrow();
    await expect(getGalleryItems('1')).rejects.toThrow();
  });
});
