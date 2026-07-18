/**
 * Unit tests for topicService.getHotspots query assembly — focused on the
 * additive `tag_id` param that filters hotspots by pool tags.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getHotspots } from './topicService';

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
