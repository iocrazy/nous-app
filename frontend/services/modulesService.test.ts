import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({}) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

import { fetchModulesStatus, __resetModulesStatusCache } from './modulesService';

describe('fetchModulesStatus', () => {
  afterEach(() => {
    __resetModulesStatusCache();
    vi.restoreAllMocks();
  });

  it('bounds the request with an abort signal', async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ modules: [{ id: 'shares', enabled: true, visible: true }] }),
    });
    vi.stubGlobal('fetch', fetchSpy);

    await fetchModulesStatus();

    const init = fetchSpy.mock.calls[0][1];
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it('returns null when the request aborts, so callers apply fail defaults', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('The operation timed out.', 'TimeoutError')),
    );

    await expect(fetchModulesStatus()).resolves.toBeNull();
  });
});
