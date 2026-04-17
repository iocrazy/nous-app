/**
 * Unit tests for resourceService — covers the endpoints already migrated
 * to apiClient. The Supabase-direct helpers (fetchFolders, renameFolder,
 * etc.) are not covered here; they require a mocked Supabase client.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getFolderContentCount,
  restoreFolder,
  trashFolder,
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
