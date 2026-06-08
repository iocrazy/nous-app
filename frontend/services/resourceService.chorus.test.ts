import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setResourceChorus } from './resourceService';

vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn().mockResolvedValue({}) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

describe('setResourceChorus', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('PUTs the chorus ms and returns the updated resource', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: { id: '1', chorus_start_ms: 42000 } }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const res = await setResourceChorus('1', 42000);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/api/v1/resources/1/chorus',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ chorus_start_ms: 42000 }),
      }),
    );
    expect(res.chorus_start_ms).toBe(42000);
  });

  it('PUTs null to clear', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ data: { id: '1', chorus_start_ms: null } }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await setResourceChorus('1', null);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/api/v1/resources/1/chorus',
      expect.objectContaining({ body: JSON.stringify({ chorus_start_ms: null }) }),
    );
  });

  it('throws on non-ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    await expect(setResourceChorus('1', 1000)).rejects.toThrow();
  });
});
