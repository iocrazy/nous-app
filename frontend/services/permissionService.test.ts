/**
 * Unit tests for permissionService — URL/query shape and envelope unwrapping
 * for /resources/permissions. The route declares `Envelope[ResourcePermissions]`,
 * so every stub below is the enveloped wire shape.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchEffectiveRole } from './permissionService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('fetchEffectiveRole', () => {
  it('threads object_type/id + team_id as query params', async () => {
    const spy = stubJson({ success: true, data: { role: 'admin', capabilities: ['read', 'write'] } });
    await fetchEffectiveRole('resource', 'r-1', 't-9');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('object_type=resource');
    expect(url).toContain('object_id=r-1');
    expect(url).toContain('team_id=t-9');
  });

  it('unwraps the { success, data } envelope', async () => {
    stubJson({ success: true, data: { role: 'owner', capabilities: ['*'] } });
    const result = await fetchEffectiveRole('resource', 'r-1', 't-9');
    expect(result.role).toBe('owner');
    expect(result.capabilities).toEqual(['*']);
  });
});
