import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn(async () => ({ Authorization: 'Bearer t' })) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
// Rest-typed so the `(...a: unknown[])` forwarders below type-check and so
// `mock.calls[n][i]` is indexable — a zero-arg `vi.fn` infers an empty tuple.
const createAsset = vi.fn(async (..._a: unknown[]) => ({ id: '900' }));
const attachFile = vi.fn(async (..._a: unknown[]) => ({}));
const updateAsset = vi.fn(async (..._a: unknown[]) => ({}));
vi.mock('./assetsService', () => ({ createAsset: (...a: unknown[]) => createAsset(...a), attachFile: (...a: unknown[]) => attachFile(...a), updateAsset: (...a: unknown[]) => updateAsset(...a) }));

import {
  fetchPrompts, langAvailability, paramChips, promptText, ratioFromParams, saveAsTemplate, thumbSrc,
} from './promptsService';

const ok = (data: unknown) => new Response(JSON.stringify({ success: true, data }), { status: 200, headers: { 'Content-Type': 'application/json' } });

// File scope, not inside one describe: `tests/setup.ts` restores SPIES after
// every test but leaves `vi.fn()` call history intact, so a per-describe reset
// would let the first saveAsTemplate call be the one the second test asserts on.
beforeEach(() => { vi.restoreAllMocks(); createAsset.mockClear(); attachFile.mockClear(); updateAsset.mockClear(); });

describe('fetchPrompts', () => {
  it('builds the query from options and unwraps the envelope', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ items: [], total: 0, by_form: {}, by_origin: {} }));
    await fetchPrompts('9000', { segment: 'project', projectId: '55', form: 'album', origin: 'captioned', q: 'rain', limit: 20, offset: 40 });
    const url = new URL(String(spy.mock.calls[0][0]));
    expect(url.pathname).toBe('/api/v1/prompts');
    expect(Object.fromEntries(url.searchParams)).toEqual({ scope_id: '9000', segment: 'project', project_id: '55', form: 'album', origin: 'captioned', q: 'rain', limit: '20', offset: '40' });
  });

  it('omits empty filters', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ items: [], total: 0, by_form: {}, by_origin: {} }));
    await fetchPrompts('9000', { q: '   ', form: null });
    const url = new URL(String(spy.mock.calls[0][0]));
    expect(Object.fromEntries(url.searchParams)).toEqual({ scope_id: '9000', segment: 'mine' });
  });
});

describe('helpers', () => {
  const src = { positive_en: 'rain', positive_zh: null, negative_en: null, negative_zh: '模糊' };
  it('promptText falls back to the other language and says which it showed', () => {
    expect(promptText(src, 'en')).toEqual({ positive: 'rain', negative: '模糊', shownLang: 'en' });
    expect(promptText(src, 'zh')).toEqual({ positive: 'rain', negative: '模糊', shownLang: 'en' });
    expect(promptText({ positive_en: null, positive_zh: null, negative_en: null, negative_zh: null }, 'en')).toEqual({ positive: '', negative: null, shownLang: null });
  });
  it('langAvailability', () => {
    expect(langAvailability(src)).toBe('en');
    expect(langAvailability({ positive_en: 'a', positive_zh: 'b' })).toBe('both');
    expect(langAvailability({ positive_en: null, positive_zh: null })).toBe('none');
  });
  it('ratioFromParams reduces width/height', () => {
    expect(ratioFromParams({ width: 832, height: 1216 })).toBe('13:19');
    expect(ratioFromParams({ width: 1920, height: 1080 })).toBe('16:9');
    expect(ratioFromParams({ width: 1024, height: 1024 })).toBe('1:1');
    expect(ratioFromParams({ steps: 3 })).toBeNull();
    expect(ratioFromParams(null)).toBeNull();
  });
  it('paramChips keeps a fixed order and skips absent keys', () => {
    expect(paramChips({ model: 'krea', steps: 28, cfg: 7, width: 1920, height: 1080, sampler: 'DPM++ 2M', seed: 8813 }))
      .toEqual(['16:9', '1920×1080', 'steps 28', 'cfg 7', 'seed 8813', 'DPM++ 2M', 'krea']);
    expect(paramChips({})).toEqual([]);
    expect(paramChips(null)).toEqual([]);
  });
  it('thumbSrc absolutizes api paths only', () => {
    expect(thumbSrc('/api/v1/resources/1/cover')).toBe('https://api.test/api/v1/resources/1/cover');
    expect(thumbSrc('https://x/y.png')).toBe('https://x/y.png');
    expect(thumbSrc(null)).toBe('');
  });

  it('thumbSrc carries the media token only on album slide URLs', () => {
    // Slides are served by an authenticated endpoint; a bare <img> cannot send
    // Bearer, so the token rides along as ?token= exactly like SlidePlayer.
    expect(thumbSrc('/api/v1/media/9/slides/002.jpg', 'tok/1')).toBe(
      'https://api.test/api/v1/media/9/slides/002.jpg?token=tok%2F1',
    );
    expect(thumbSrc('/api/v1/media/9/slides/a.jpg?x=1', 'tok')).toBe('https://api.test/api/v1/media/9/slides/a.jpg?x=1&token=tok');
    // Covers are public — no token leaks into their URL.
    expect(thumbSrc('/api/v1/resources/1/cover', 'tok')).toBe('https://api.test/api/v1/resources/1/cover');
    // No session token yet → the plain URL (the old behaviour), never "token=null".
    expect(thumbSrc('/api/v1/media/9/slides/002.jpg', null)).toBe('https://api.test/api/v1/media/9/slides/002.jpg');
  });
});

describe('saveAsTemplate', () => {
  it('creates the asset, attaches examples to the examples slot, sets the cover', async () => {
    const r = await saveAsTemplate('9000', { title: 'Courier dawn', group: 'Lighting', positive: 'p', negative: 'n', exampleResourceIds: ['501', '502'] });
    expect(r).toEqual({ assetId: '900' });
    expect(createAsset).toHaveBeenCalledWith('9000', {
      asset_type: 'prompt', name: 'Courier dawn', source: 'manual',
      prompt_positive: 'p', prompt_negative: 'n', prompt_positive_zh: null, prompt_negative_zh: null,
      tags: { group: ['Lighting'] },
    });
    expect(attachFile.mock.calls.map((c) => c[2])).toEqual([{ resource_id: '501', slot: 'examples' }, { resource_id: '502', slot: 'examples' }]);
    expect(updateAsset).toHaveBeenCalledWith('9000', '900', { cover_file_id: '501' });
  });
  it('empty group means no tags and no cover call without examples', async () => {
    await saveAsTemplate('9000', { title: 't', group: '  ', positive: 'p', negative: '', exampleResourceIds: [] });
    expect(createAsset.mock.calls[0][1]).toMatchObject({ tags: {}, prompt_negative: null });
    expect(attachFile).not.toHaveBeenCalled();
    expect(updateAsset).not.toHaveBeenCalled();
  });
});
