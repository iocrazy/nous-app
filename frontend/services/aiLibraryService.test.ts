/**
 * Unit tests for the slim subset of aiLibraryService that this PR
 * adds — getAgentDashboard. The rest of the service is exercised
 * indirectly by component tests.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { aiLibraryService } from './aiLibraryService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

function stubFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('aiLibraryService.getAgentDashboard', () => {
  it('GETs /agents/:slug/dashboard and returns the envelope', async () => {
    const body = {
      agent: { slug: 'ceo', name: 'CEO', persistent: true },
      latest_run: null,
      run_activity_14d: Array.from({ length: 14 }, (_, i) => ({
        date: `2026-04-${String(13 + i).padStart(2, '0')}`,
        count: 0,
      })),
      tasks_by_status_14d: {},
      success_rate_14d: [],
      costs_14d: {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        total_cost_cents: 0,
        run_count: 0,
      },
      recent_tasks: [],
      recent_runs: [],
    };
    const spy = stubFetch(body);

    const out = await aiLibraryService.getAgentDashboard('ceo');

    expect(out).toEqual(body);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('/agents/ceo/dashboard');
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method ?? 'GET').toBe('GET');
  });

  it('encodes slug so weird characters do not break the URL', async () => {
    stubFetch({});
    const spy = vi.mocked(globalThis.fetch);
    await aiLibraryService.getAgentDashboard('a/b c').catch(() => undefined);
    expect(spy.mock.calls[0][0]).toContain('/agents/a%2Fb%20c/dashboard');
  });

  it('throws on 404', async () => {
    stubFetch({ detail: 'agent not found' }, 404);
    await expect(aiLibraryService.getAgentDashboard('missing')).rejects.toThrow(/404/);
  });
});
