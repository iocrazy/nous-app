/**
 * `usageService.getEfficiency` —— 3c §3.3 的效率读面客户端。
 *
 * 响应体照抄生产 wire 形状（`backend/app/schemas/efficiency.py`）：`from` 是保留字，
 * 后端用 alias 发的就是 `from`；`avg_run_ms` / `cost_per_deliverable_cents` 可空，
 * 而「0 件产出的单价」是 null 不是 0。拒绝一律走 `ErrorResponse` 外壳，可分支的码
 * 在 `details.code`（CLAUDE.md 2026-09-09）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

const { usageService, UsageRequestError } = await import('./usageService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const WIRE = {
  scope: 'user',
  group_by: 'model',
  from: '2026-08-16T00:00:00Z',
  to: '2026-09-15T00:00:00Z',
  groups: [
    {
      key: 'doubao-seed-1-6', label: 'doubao-seed-1-6', run_count: 12, failed_runs: 1,
      avg_run_ms: 41230, tool_calls: 40, tool_errors: 4, tool_error_rate: 0.1,
      deliverables: 8, cost_cents: 40.5, cost_per_deliverable_cents: 5.0625,
    },
    {
      key: 'qwen-max', label: 'qwen-max', run_count: 2, failed_runs: 0,
      avg_run_ms: null, tool_calls: 0, tool_errors: 0, tool_error_rate: 0.0,
      deliverables: 0, cost_cents: 0.0, cost_per_deliverable_cents: null,
    },
  ],
  turn_end_reasons: { completed: 9, awaiting_input: 2, error: 1 },
};

describe('usageService.getEfficiency', () => {
  it('asks the ai-library efficiency endpoint with the scope and grouping', async () => {
    fetchMock.mockResolvedValue(json(200, WIRE));
    await usageService.getEfficiency({ scope: 'user', groupBy: 'agent' });
    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.pathname).toBe('/api/v1/ai-library/usage/efficiency');
    expect(url.searchParams.get('scope')).toBe('user');
    expect(url.searchParams.get('group_by')).toBe('agent');
    expect(url.searchParams.get('id')).toBeNull();
  });

  it('defaults the grouping to model and passes the window through', async () => {
    fetchMock.mockResolvedValue(json(200, WIRE));
    await usageService.getEfficiency({
      scope: 'team', id: 424242424242, from: '2026-08-01T00:00:00Z', to: '2026-09-01T00:00:00Z',
    });
    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.searchParams.get('group_by')).toBe('model');
    expect(url.searchParams.get('id')).toBe('424242424242');
    expect(url.searchParams.get('from')).toBe('2026-08-01T00:00:00Z');
    expect(url.searchParams.get('to')).toBe('2026-09-01T00:00:00Z');
  });

  it('keeps a null cost-per-deliverable null instead of coercing it to zero', async () => {
    fetchMock.mockResolvedValue(json(200, WIRE));
    const out = await usageService.getEfficiency({ scope: 'user' });
    expect(out.groups[1].cost_per_deliverable_cents).toBeNull();
    expect(out.groups[1].avg_run_ms).toBeNull();
    expect(out.turn_end_reasons).toEqual({ completed: 9, awaiting_input: 2, error: 1 });
    expect(out.group_by).toBe('model');
  });

  it('raises the typed code the route chose, not the whole envelope', async () => {
    fetchMock.mockResolvedValue(
      json(503, {
        success: false,
        error: 'Service Unavailable',
        code: 'http_503',
        request_id: 'req-1',
        details: { code: 'efficiency_unavailable' },
      }),
    );
    await expect(usageService.getEfficiency({ scope: 'user' })).rejects.toMatchObject({
      code: 'efficiency_unavailable',
      status: 503,
    });
  });

  it('carries a 400 range refusal as its own code so the caller can name the window', async () => {
    fetchMock.mockResolvedValue(
      json(400, {
        success: false, error: 'Bad Request', code: 'http_400',
        details: { code: 'range_too_long' },
      }),
    );
    const err = await usageService.getEfficiency({ scope: 'user' }).catch((e) => e);
    expect(err).toBeInstanceOf(UsageRequestError);
    expect(err.code).toBe('range_too_long');
  });

  it('falls back to http_<status> when the body typed nothing', async () => {
    // 网关的 HTML 502 也要能被分支——退不出码不等于可以把原文渲给用户。
    fetchMock.mockResolvedValue(new Response('<html>bad gateway</html>', { status: 502 }));
    const err = await usageService.getEfficiency({ scope: 'user' }).catch((e) => e);
    expect(err.code).toBe('http_502');
  });
});
