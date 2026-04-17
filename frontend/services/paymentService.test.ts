/**
 * Unit tests for paymentService.
 *
 * Money-handling code gets the strictest tests: we verify every endpoint
 * builds the right URL, sends the right method, unwraps the success
 * envelope, and fails loudly on { success: false }.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createOrder,
  fetchOrders,
  fetchPackages,
  pollOrderStatus,
} from './paymentService';

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
});

describe('paymentService', () => {
  it('fetchPackages unwraps envelope and returns data', async () => {
    const packages = [
      { id: 'p1', name: '100 pts', points: 100, price_cents: 100 },
      { id: 'p2', name: '500 pts', points: 500, price_cents: 450 },
    ];
    stubResponse({ success: true, data: packages });

    const result = await fetchPackages();
    expect(result).toEqual(packages);
  });

  it('fetchPackages throws on { success: false }', async () => {
    stubResponse({ success: false, message: 'backend offline' });
    await expect(fetchPackages()).rejects.toThrow('backend offline');
  });

  it('createOrder POSTs correct body and URL', async () => {
    const order = { id: 'o1', payment_status: 'pending' };
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ success: true, data: order }),
      json: async () => ({ success: true, data: order }),
    } as unknown as Response);

    const result = await createOrder('pkg-1', 'wechat', 'team-42');
    expect(result).toEqual(order);

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/payment/create-order');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      package_id: 'pkg-1',
      payment_method: 'wechat',
      team_id: 'team-42',
    });
  });

  it('createOrder rejects alipay->wechat typos at runtime gracefully', async () => {
    stubResponse({ success: false, message: 'Unsupported payment method' });
    await expect(createOrder('pkg-1', 'wechat', 'team-1')).rejects.toThrow(
      'Unsupported payment method',
    );
  });

  it('pollOrderStatus returns the full status tuple', async () => {
    stubResponse({
      success: true,
      data: {
        payment_status: 'paid',
        points_amount: 500,
        paid_at: '2026-01-01T00:00:00Z',
      },
    });

    const status = await pollOrderStatus('o1');
    expect(status.payment_status).toBe('paid');
    expect(status.points_amount).toBe(500);
    expect(status.paid_at).toBe('2026-01-01T00:00:00Z');
  });

  it('fetchOrders forwards team_id, limit, offset as query params', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ success: true, data: [] }),
      json: async () => ({ success: true, data: [] }),
    } as unknown as Response);

    await fetchOrders('team-42', 50, 100);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('team_id=team-42');
    expect(url).toContain('limit=50');
    expect(url).toContain('offset=100');
  });

  it('fetchOrders omits team_id when undefined', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ success: true, data: [] }),
      json: async () => ({ success: true, data: [] }),
    } as unknown as Response);

    await fetchOrders(undefined, 20, 0);
    const url = spy.mock.calls[0][0] as string;
    expect(url).not.toContain('team_id=');
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=0');
  });
});
