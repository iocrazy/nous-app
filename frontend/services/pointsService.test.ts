/**
 * Unit tests for pointsService.
 *
 * Pins the envelope-unwrap contract — the backend uses non-uniform field
 * names (data / transactions / pricing), and this service must shield
 * callers from that inconsistency.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  adjustPoints,
  checkQuota,
  fetchPointsBalance,
  fetchPointsPricing,
  fetchPointsTransactions,
  fetchUsageStats,
} from './pointsService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubResponse(body: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
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

describe('fetchPointsBalance', () => {
  it('unwraps success + data', async () => {
    stubResponse({
      success: true,
      data: { team_id: 't1', points_balance: 100, storage_used_bytes: 0 },
    });
    const quota = await fetchPointsBalance('t1');
    expect(quota.points_balance).toBe(100);
  });

  it('forwards team_id as query param', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ success: true, data: { points_balance: 0 } }),
      json: async () => ({ success: true, data: { points_balance: 0 } }),
    } as unknown as Response);

    await fetchPointsBalance('team-xyz');
    expect(spy.mock.calls[0][0]).toContain('team_id=team-xyz');
  });

  it('rejects on success=false', async () => {
    stubResponse({ success: false, message: 'quota missing' });
    await expect(fetchPointsBalance()).rejects.toThrow('quota missing');
  });
});

describe('fetchPointsTransactions', () => {
  it('unwraps the `transactions` field, not `data`', async () => {
    stubResponse({
      success: true,
      transactions: [
        { id: 1, amount: -10, type: 'consume' },
        { id: 2, amount: 5, type: 'refund' },
      ],
    });
    const tx = await fetchPointsTransactions();
    expect(tx).toHaveLength(2);
    expect(tx[0].type).toBe('consume');
  });

  it('threads through filters', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ success: true, transactions: [] }),
      json: async () => ({ success: true, transactions: [] }),
    } as unknown as Response);

    await fetchPointsTransactions('t1', 10, 20, 'consume', 'ai_transcription');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('team_id=t1');
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=20');
    expect(url).toContain('type=consume');
    expect(url).toContain('reference_type=ai_transcription');
  });
});

describe('fetchPointsPricing', () => {
  it('unwraps the `pricing` field', async () => {
    stubResponse({
      success: true,
      pricing: [{ action_type: 'ai_transcription', points_cost: 10 }],
    });
    const pricing = await fetchPointsPricing();
    expect(pricing).toHaveLength(1);
    expect(pricing[0].action_type).toBe('ai_transcription');
  });
});

describe('checkQuota', () => {
  it('builds GET with action_type, count, team_id query', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({
          success: true,
          data: { allowed: true, points_cost: 10, current_balance: 100 },
        }),
      json: async () => ({
        success: true,
        data: { allowed: true, points_cost: 10, current_balance: 100 },
      }),
    } as unknown as Response);

    await checkQuota('ai_summary', 2, 't1');
    const [url, init] = spy.mock.calls[0];
    expect((init as RequestInit).method).toBe('GET');
    const urlStr = url as string;
    expect(urlStr).toContain('action_type=ai_summary');
    expect(urlStr).toContain('count=2');
    expect(urlStr).toContain('team_id=t1');
  });

  it('returns allowed=true when RPC says so', async () => {
    stubResponse({
      success: true,
      data: { allowed: true, points_cost: 0, current_balance: 1000 },
    });
    const result = await checkQuota('free_action');
    expect(result.allowed).toBe(true);
  });
});

describe('adjustPoints', () => {
  it('POSTs amount + description + team_id', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ success: true, new_balance: 1500 }),
      json: async () => ({ success: true, new_balance: 1500 }),
    } as unknown as Response);

    const result = await adjustPoints('t1', 500, 'Admin top-up');
    expect(result.new_balance).toBe(1500);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      team_id: 't1',
      amount: 500,
      description: 'Admin top-up',
    });
  });

  it('throws on { success: false }', async () => {
    stubResponse({ success: false, message: 'not authorized' });
    await expect(adjustPoints('t1', 100, 'x')).rejects.toThrow(
      'not authorized',
    );
  });
});

describe('fetchUsageStats', () => {
  it('returns the data payload', async () => {
    stubResponse({ success: true, data: { total_spent: 123 } });
    const stats = (await fetchUsageStats('t1')) as { total_spent: number };
    expect(stats.total_spent).toBe(123);
  });
});
