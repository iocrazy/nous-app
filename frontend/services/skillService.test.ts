/**
 * Unit tests for skillService — covers the 30s cache + cache-invalidation
 * contract on mutations, plus the Envelope<T>.data unwrap.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createSkill,
  deleteSkill,
  fetchSkillDetail,
  fetchSkills,
  invalidateSkillsCache,
  updateSkill,
} from './skillService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubResponse(body: unknown, status: number = 200): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
  invalidateSkillsCache();
});

describe('fetchSkills cache', () => {
  it('caches the first result and reuses it for the same projectId', async () => {
    stubResponse({ data: [{ id: 's1', name: 'Skill' }] });

    const fetchSpy = vi.mocked(globalThis.fetch);
    const first = await fetchSkills('p1');
    expect(first).toHaveLength(1);
    const callsAfterFirst = fetchSpy.mock.calls.length;

    // Second call should hit cache — no new fetch.
    const second = await fetchSkills('p1');
    expect(fetchSpy.mock.calls.length).toBe(callsAfterFirst);
    expect(second).toEqual(first);
  });

  it('does not cache category-filtered queries', async () => {
    stubResponse({ data: [{ id: 's1' }] });
    await fetchSkills('p1', 'filter');

    // Still no cache — second call must hit the network.
    stubResponse({ data: [{ id: 's2' }] });
    const result = await fetchSkills('p1', 'filter');
    expect(result[0].id).toBe('s2');
  });

  it('refreshes cache for a different projectId', async () => {
    stubResponse({ data: [{ id: 's-team-a' }] });
    const first = await fetchSkills('team-a');

    stubResponse({ data: [{ id: 's-team-b' }] });
    const second = await fetchSkills('team-b');

    expect(first[0].id).toBe('s-team-a');
    expect(second[0].id).toBe('s-team-b');
  });

  it('invalidateSkillsCache forces the next fetch to hit the network', async () => {
    stubResponse({ data: [{ id: 's1' }] });
    await fetchSkills('p1');

    invalidateSkillsCache();
    stubResponse({ data: [{ id: 's2' }] });
    const refreshed = await fetchSkills('p1');
    expect(refreshed[0].id).toBe('s2');
  });
});

describe('mutations invalidate cache', () => {
  it('createSkill invalidates cache', async () => {
    stubResponse({ data: [{ id: 's1' }] });
    await fetchSkills();

    stubResponse({ data: { id: 's2', name: 'New' } });
    await createSkill({ name: 'New', content_md: 'x' });

    stubResponse({ data: [{ id: 's1' }, { id: 's2' }] });
    const refreshed = await fetchSkills();
    expect(refreshed).toHaveLength(2);
  });

  it('updateSkill invalidates cache', async () => {
    stubResponse({ data: [{ id: 's1', name: 'Old' }] });
    await fetchSkills();

    stubResponse({ data: { id: 's1', name: 'Renamed' } });
    await updateSkill('s1', { name: 'Renamed' });

    stubResponse({ data: [{ id: 's1', name: 'Renamed' }] });
    const refreshed = await fetchSkills();
    expect(refreshed[0].name).toBe('Renamed');
  });

  it('deleteSkill invalidates cache', async () => {
    stubResponse({ data: [{ id: 's1' }] });
    await fetchSkills();

    stubResponse(undefined);
    await deleteSkill('s1');

    stubResponse({ data: [] });
    const refreshed = await fetchSkills();
    expect(refreshed).toHaveLength(0);
  });
});

describe('fetchSkillDetail', () => {
  it('unwraps the envelope', async () => {
    stubResponse({ data: { id: 's1', name: 'Detail', content_md: 'body' } });
    const skill = await fetchSkillDetail('s1');
    expect(skill.name).toBe('Detail');
  });
});
