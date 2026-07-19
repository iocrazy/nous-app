import { beforeEach, describe, expect, it, vi } from 'vitest';

import { usageService } from './usageService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer x' }),
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

describe('usageService.getSummary', () => {
  it('builds the query string with team_id + group_by and keeps snowflake a string', async () => {
    const spy = stubFetch({ team_id: '900123456789012345' });
    await usageService.getSummary('900123456789012345', {
      from: '2026-07-01T00:00:00Z',
      to: '2026-07-18T00:00:00Z',
      groupBy: 'attribution',
    });
    const url = String(spy.mock.calls[0][0]);
    expect(url).toContain('https://api.test/api/v1/usage/summary?');
    expect(url).toContain('team_id=900123456789012345');
    expect(url).toContain('group_by=attribution');
    expect(url).toContain('from=');
    expect(url).toContain('to=');
  });

  it('defaults group_by to model', async () => {
    const spy = stubFetch({});
    await usageService.getSummary('900');
    expect(String(spy.mock.calls[0][0])).toContain('group_by=model');
  });

  it('throws on non-ok', async () => {
    stubFetch('nope', 404);
    await expect(usageService.getSummary('900')).rejects.toThrow('404');
  });
});

describe('usageService.getIssueUsage', () => {
  it('GETs the per-issue endpoint', async () => {
    const spy = stubFetch({ issue_id: '555', total_tokens: 70 });
    const out = await usageService.getIssueUsage('555');
    expect(String(spy.mock.calls[0][0])).toBe('https://api.test/api/v1/usage/issues/555');
    expect(out.total_tokens).toBe(70);
  });
});

describe('usageService budget', () => {
  it('GETs the team budget', async () => {
    const spy = stubFetch({ team_id: '900', monthly_budget_cents: 500 });
    await usageService.getTeamBudget('900');
    expect(String(spy.mock.calls[0][0])).toBe('https://api.test/api/v1/teams/900/ai-budget');
  });

  it('PUTs the team budget with the cents body', async () => {
    const spy = stubFetch({ team_id: '900', monthly_budget_cents: 1000 });
    await usageService.setTeamBudget('900', 1000);
    const [, init] = spy.mock.calls[0];
    expect((init as RequestInit).method).toBe('PUT');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      monthly_budget_cents: 1000,
    });
  });

  it('PUTs null for unlimited', async () => {
    const spy = stubFetch({ team_id: '900', monthly_budget_cents: null });
    await usageService.setTeamBudget('900', null);
    const [, init] = spy.mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      monthly_budget_cents: null,
    });
  });
});
