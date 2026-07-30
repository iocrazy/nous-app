/**
 * Unit tests for topicService.getHotspots query assembly — focused on the
 * additive `tag_id` param that filters hotspots by pool tags.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getHotspots, getHotspotDates, getSourceHealth } from './topicService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ 'Content-Type': 'application/json' }),
  parseShareLink: vi.fn(),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

function stubFetch() {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify({ hotspots: [] }),
    json: async () => ({ hotspots: [] }),
  } as unknown as Response);
}

function lastUrl(spy: ReturnType<typeof stubFetch>): string {
  const call = spy.mock.calls[spy.mock.calls.length - 1];
  return String(call[0]);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('getHotspots tag filter', () => {
  it('appends tag_id param joined by comma', async () => {
    const spy = stubFetch();
    await getHotspots(undefined, undefined, undefined, 'all', undefined, ['1', '2']);
    // URLSearchParams encodes the comma as %2C.
    expect(lastUrl(spy)).toContain('tag_id=1%2C2');
  });

  it('omits tag_id when no tag ids are given', async () => {
    const spy = stubFetch();
    await getHotspots(undefined, undefined, undefined, 'all', undefined, []);
    expect(lastUrl(spy)).not.toContain('tag_id');
  });

  it('omits tag_id when the arg is undefined (backward compatible)', async () => {
    const spy = stubFetch();
    await getHotspots(undefined, undefined, undefined, 'all', undefined);
    expect(lastUrl(spy)).not.toContain('tag_id');
  });
});

// Regression coverage for the pre-existing "TypeError: u is not iterable"
// crash (K1/K2 warm-paper-palette reports; module-accent-probe excluded 4
// inspiration probes because of it). Root cause: these getters blindly cast
// `response.<key>` to an array with `as T[]` — a response missing that key
// (stub shape mismatch, or any future API contract drift) produced
// `undefined`, which flowed straight into React state and then crashed
// inside consumers that iterate it unconditionally (`hotspotRanking.ts`'s
// `partitionBySignal` via `for...of`, `TopicFilterBar.tsx`'s
// `dates.slice(...)`). Fixed by routing every list-shaped response through
// the `toArray` helper so a malformed payload degrades to `[]` instead of
// `undefined`. See `pages/TopicInspirationPage.test.tsx` for the page-level
// regression proving the crash itself no longer reproduces.
function respondWith(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

describe('list-shaped getters degrade to [] on malformed responses', () => {
  // Silence (and inspect) the `toArray` shape-drift warning added per review
  // — the [] fallback must not be a *silent* swallow (CLAUDE.md's "catch 静默
  // 吞错" rule; matches resourceService.ts's checkDuplicatesBatch /
  // workflowService.ts's fetchTemplates precedents in the same dir).
  let warnSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  it('getHotspots returns [] and warns when the response omits `hotspots`', async () => {
    respondWith({});
    await expect(getHotspots()).resolves.toEqual([]);
    expect(warnSpy).toHaveBeenCalledWith(
      'getHotspots: unexpected non-array response shape',
      undefined,
    );
  });

  it('getHotspots returns [] and warns when `hotspots` is null', async () => {
    respondWith({ hotspots: null });
    await expect(getHotspots()).resolves.toEqual([]);
    expect(warnSpy).toHaveBeenCalledWith(
      'getHotspots: unexpected non-array response shape',
      null,
    );
  });

  it('getHotspots still returns the real list on a well-formed response, no warning', async () => {
    respondWith({ hotspots: [{ id: '1' }] });
    await expect(getHotspots()).resolves.toEqual([{ id: '1' }]);
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it('getHotspotDates returns [] and warns when the response omits `dates`', async () => {
    respondWith({});
    await expect(getHotspotDates()).resolves.toEqual([]);
    expect(warnSpy).toHaveBeenCalledWith(
      'getHotspotDates: unexpected non-array response shape',
      undefined,
    );
  });

  it('getSourceHealth returns [] and warns when the response omits `sources`', async () => {
    respondWith({});
    await expect(getSourceHealth()).resolves.toEqual([]);
    expect(warnSpy).toHaveBeenCalledWith(
      'getSourceHealth: unexpected non-array response shape',
      undefined,
    );
  });
});
