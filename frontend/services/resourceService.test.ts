/**
 * Unit tests for resourceService — covers the endpoints already migrated
 * to apiClient. The Supabase-direct helpers (fetchFolders, renameFolder,
 * etc.) are not covered here; they require a mocked Supabase client.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getFolderContentCount,
  getResourceLyrics,
  restoreFolder,
  trashFolder,
  uploadResourceCover,
  uploadResourceLyrics,
} from './resourceService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  supabase: {},
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));
vi.mock('../utils/mediaUrl', () => ({
  buildMediaUrl: (u: string) => u,
}));

function stubResponse(body: unknown, status: number = 200): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('trashFolder', () => {
  it('POSTs to /folders/:id/trash', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => '{}',
      json: async () => ({}),
    } as unknown as Response);

    await trashFolder('folder-42');
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe(
      'https://api.test/api/v1/resources/folders/folder-42/trash',
    );
    expect((init as RequestInit).method).toBe('POST');
  });

  it('throws ApiError on non-2xx', async () => {
    stubResponse({ detail: 'not found' }, 404);
    await expect(trashFolder('nope')).rejects.toMatchObject({ status: 404 });
  });
});

describe('restoreFolder', () => {
  it('POSTs to /folders/:id/restore', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => '{}',
      json: async () => ({}),
    } as unknown as Response);

    await restoreFolder('folder-7');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/resources/folders/folder-7/restore',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });
});

describe('getFolderContentCount', () => {
  it('unwraps the `data` envelope', async () => {
    stubResponse({ data: { resource_count: 3, subfolder_count: 1 } });
    const result = await getFolderContentCount('folder-1');
    expect(result.resource_count).toBe(3);
    expect(result.subfolder_count).toBe(1);
  });

  it('propagates apiClient errors', async () => {
    stubResponse({ error: 'boom' }, 500);
    await expect(getFolderContentCount('folder-x')).rejects.toMatchObject({
      status: 500,
    });
  });
});

describe('getResourceLyrics', () => {
  it('GETs /resources/:id/lyrics and returns data', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ success: true, data: { lrc: 'x', lines: [] } }),
      json: async () => ({ success: true, data: { lrc: 'x', lines: [] } }),
    } as unknown as Response);

    const out = await getResourceLyrics('77');
    expect(out).toEqual({ lrc: 'x', lines: [] });
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/resources/77/lyrics',
    );
  });

  it('returns null on non-ok', async () => {
    stubResponse({}, 404);
    expect(await getResourceLyrics('77')).toBeNull();
  });
});

describe('uploadResourceCover', () => {
  it('POSTs multipart to /resources/:id/cover and unwraps data', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ success: true, data: { id: '77' } }),
      json: async () => ({ success: true, data: { id: '77' } }),
    } as unknown as Response);

    const file = new File(['img'], 'cover.jpg', { type: 'image/jpeg' });
    const out = await uploadResourceCover('77', file);
    expect(out).toEqual({ id: '77' });
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/resources/77/cover');
    expect((init as RequestInit).method).toBe('POST');
    expect((init as RequestInit).body).toBeInstanceOf(FormData);
    // multipart: Content-Type must NOT be set (browser sets boundary)
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(
      Object.keys(headers).some((k) => k.toLowerCase() === 'content-type'),
    ).toBe(false);
  });

  it('throws on non-ok', async () => {
    stubResponse({ detail: 'bad' }, 400);
    const file = new File(['img'], 'cover.jpg', { type: 'image/jpeg' });
    await expect(uploadResourceCover('77', file)).rejects.toThrow();
  });
});

describe('uploadResourceLyrics', () => {
  it('POSTs multipart to /resources/:id/lyrics and returns lyrics_json', async () => {
    const lyrics = { lrc: 'x', lines: [{ text: 'hi', line_start_ms: 0 }] };
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ success: true, data: { lyrics_json: lyrics } }),
      json: async () => ({ success: true, data: { lyrics_json: lyrics } }),
    } as unknown as Response);

    const file = new File(['lrc'], 'song.lrc', { type: 'text/plain' });
    const out = await uploadResourceLyrics('77', file);
    expect(out).toEqual(lyrics);
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/resources/77/lyrics');
    expect((init as RequestInit).method).toBe('POST');
    expect((init as RequestInit).body).toBeInstanceOf(FormData);
  });

  it('throws with detail on non-ok', async () => {
    stubResponse({ detail: 'invalid lrc' }, 422);
    const file = new File(['lrc'], 'song.lrc', { type: 'text/plain' });
    await expect(uploadResourceLyrics('77', file)).rejects.toThrow('invalid lrc');
  });
});
