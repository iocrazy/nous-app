import { afterEach, describe, expect, it, vi } from 'vitest';

// resourceService.ts imports: supabase (side-effect), getApiUrl from
// ../utils/apiConfig, getAuthHeaders from ./parserService. Mock all three.
vi.mock('../supabaseClient', () => ({ supabase: {} }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer T' }),
}));

import { saveTextAsNewVersion, overwriteVersionContent } from './resourceService';

afterEach(() => vi.restoreAllMocks());

describe('saveTextAsNewVersion', () => {
  it('POSTs a File built from the text to the versions endpoint', async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(String(url)).toContain('/api/v1/resources/10/versions');
      expect(init.method).toBe('POST');
      const body = init.body as FormData;
      const f = body.get('file') as File;
      expect(await f.text()).toBe('hello world');
      expect(f.name).toBe('notes.md');
      return { ok: true, json: async () => ({ data: { id: 'v2' } }) } as Response;
    });
    vi.stubGlobal('fetch', fetchMock);
    const r = await saveTextAsNewVersion('10', 'hello world', 'notes.md', 'text/markdown');
    expect(r.id).toBe('v2');
  });
});

describe('overwriteVersionContent', () => {
  it('PUTs a File built from the text to the content endpoint', async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(String(url)).toContain('/api/v1/resources/10/versions/77/content');
      expect(init.method).toBe('PUT');
      const f = (init.body as FormData).get('file') as File;
      expect(await f.text()).toBe('overwritten');
      return { ok: true, json: async () => ({ data: { id: '77' } }) } as Response;
    });
    vi.stubGlobal('fetch', fetchMock);
    const r = await overwriteVersionContent('10', '77', 'overwritten', 'a.txt', 'text/plain');
    expect(r.id).toBe('77');
  });
});
