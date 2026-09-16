/**
 * `aiLibraryService.getRunCosts` —— 一屏气泡的花费（3c §4.2）。
 *
 * 后端一批最多 50 个**去重后**的 id，超出是 400 `too_many_ids`；看不见的 run 是
 * **键省略**，不是 404。所以客户端要自己去重 + 分批，并且不能把缺席的键补成 0。
 * 响应体是生产 wire 形状：run id 是 string（snowflake BIGINT 过不了 2^53）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

const { aiLibraryService } = await import('./aiLibraryService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const row = (over: Record<string, unknown> = {}) => ({
  cost_cents: 12.5, charged_points: 30.1177, model: 'doubao-seed-1-6',
  status: 'completed', prompt_tokens: 900, completion_tokens: 120, ...over,
});

const idsOf = (call: unknown[]): string[] =>
  new URL(call[0] as string).searchParams.get('ids')!.split(',');

describe('aiLibraryService.getRunCosts', () => {
  it('sends one batch and unwraps items', async () => {
    fetchMock.mockResolvedValue(json(200, { items: { '347786145852739': row() } }));
    const out = await aiLibraryService.getRunCosts(['347786145852739']);
    expect(new URL(fetchMock.mock.calls[0][0] as string).pathname).toBe('/api/v1/ai-library/runs/costs');
    expect(out['347786145852739'].charged_points).toBe(30.1177);
  });

  it('never calls out for an empty list', async () => {
    expect(await aiLibraryService.getRunCosts([])).toEqual({});
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('deduplicates before batching so one repainted bubble does not eat a slot', async () => {
    fetchMock.mockResolvedValue(json(200, { items: {} }));
    await aiLibraryService.getRunCosts(['1', '1', '2', '']);
    expect(idsOf(fetchMock.mock.calls[0])).toEqual(['1', '2']);
  });

  it('splits more than fifty ids into batches instead of dropping the tail', async () => {
    // 简报里是 slice(0, 50)——那会让第 51 个气泡永远没有价钱且没有任何人报错。
    const ids = Array.from({ length: 120 }, (_, i) => String(1000 + i));
    fetchMock.mockImplementation(async (url: string) =>
      json(200, {
        items: Object.fromEntries(
          new URL(url).searchParams.get('ids')!.split(',').map((i) => [i, row()]),
        ),
      }),
    );
    const out = await aiLibraryService.getRunCosts(ids);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(idsOf(fetchMock.mock.calls[0])).toHaveLength(50);
    expect(idsOf(fetchMock.mock.calls[2])).toHaveLength(20);
    expect(Object.keys(out)).toHaveLength(120);
  });

  it('leaves an invisible run absent rather than inventing a zero cost', async () => {
    fetchMock.mockResolvedValue(json(200, { items: { '1': row() } }));
    const out = await aiLibraryService.getRunCosts(['1', '2']);
    expect('2' in out).toBe(false);
  });

  it('keeps a null cost and a null charged_points apart from zero', async () => {
    fetchMock.mockResolvedValue(
      json(200, { items: { '1': row({ cost_cents: null, charged_points: null, model: null, status: null }) } }),
    );
    const out = await aiLibraryService.getRunCosts(['1']);
    expect(out['1'].cost_cents).toBeNull();
    expect(out['1'].charged_points).toBeNull();
    expect(out['1'].status).toBeNull();
  });

  it('raises the typed unavailable code instead of a half batch', async () => {
    fetchMock.mockResolvedValue(
      json(503, {
        success: false, error: 'Service Unavailable', code: 'http_503',
        details: { code: 'run_costs_unavailable' },
      }),
    );
    await expect(aiLibraryService.getRunCosts(['1'])).rejects.toMatchObject({
      code: 'run_costs_unavailable',
    });
  });
});
