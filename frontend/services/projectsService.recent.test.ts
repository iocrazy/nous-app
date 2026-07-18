/**
 * fetchRecentItems — asserts the endpoint URL + limit query param and the
 * direct (no `{data}` envelope) `.items` unwrap.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('./apiClient', () => ({
  apiClient: { get: (...args: unknown[]) => get(...args) },
  apiFetch: vi.fn(),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { fetchRecentItems } from './projectsService';

beforeEach(() => {
  get.mockReset();
});

describe('fetchRecentItems', () => {
  it('GETs /projects/recent-items with the limit query and unwraps items', async () => {
    const items = [
      { kind: 'script', id: '1', name: 'Ep 1', project_id: '10', project_name: 'Show', updated_at: '2026-07-05T00:00:00+00:00' },
    ];
    get.mockResolvedValueOnce({ items });

    const out = await fetchRecentItems(5);

    expect(get).toHaveBeenCalledWith('/api/v1/projects/recent-items', {
      query: { limit: 5 },
    });
    expect(out).toEqual(items);
  });

  it('defaults the limit to 8', async () => {
    get.mockResolvedValueOnce({ items: [] });
    await fetchRecentItems();
    expect(get).toHaveBeenCalledWith('/api/v1/projects/recent-items', {
      query: { limit: 8 },
    });
  });

  it('returns [] when the response has no items', async () => {
    get.mockResolvedValueOnce({});
    expect(await fetchRecentItems()).toEqual([]);
  });
});
