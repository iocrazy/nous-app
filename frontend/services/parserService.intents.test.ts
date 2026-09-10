import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../supabaseClient', () => ({ getSupabaseAccessToken: vi.fn().mockResolvedValue(null) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

import { applyIntentFields, parseBatchLinks, parseShareLink } from './parserService';

const okFetch = () =>
  vi.fn().mockResolvedValue({ ok: true, json: async () => ({ success: true }) });

const sentBody = (fetchMock: ReturnType<typeof vi.fn>) =>
  JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);

describe('parserService AI intent fields', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('applyIntentFields writes only true-valued intents', () => {
    const body: Record<string, unknown> = {};
    applyIntentFields(body, { transcribe: true, summarize: false });
    expect(body).toEqual({ transcribe: true });
  });

  it('parseShareLink sends intent booleans and omits false ones', async () => {
    const fetchMock = okFetch();
    vi.stubGlobal('fetch', fetchMock);
    await parseShareLink('https://v.douyin.com/abc/', { analyze: true, summarize: false });
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/media/fetch');
    const body = sentBody(fetchMock);
    expect(body.analyze).toBe(true);
    expect('summarize' in body).toBe(false);
    expect('transcribe' in body).toBe(false);
  });

  it('parseBatchLinks sends intent booleans on the batch body', async () => {
    const fetchMock = okFetch();
    vi.stubGlobal('fetch', fetchMock);
    await parseBatchLinks(['https://v.douyin.com/a/'], { transcribe: true, summarize: true });
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/media/fetch/batch');
    expect(sentBody(fetchMock)).toMatchObject({ transcribe: true, summarize: true });
    expect('analyze' in sentBody(fetchMock)).toBe(false);
  });
});
