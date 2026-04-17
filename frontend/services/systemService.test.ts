/**
 * Unit tests for systemService — API shape + display/formatting helpers.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  formatBytes,
  getQueueDisplay,
  getStorageDisplay,
  getSystemStatus,
} from './systemService';

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

describe('getSystemStatus', () => {
  it('hits /system/status', async () => {
    const spy = stubJson({
      queue: { active: 0, pending: 0, scheduled: 0, status: 'online' },
      storage: {
        total_bytes: 0,
        used_bytes: 0,
        free_bytes: 0,
        percent_used: 0,
        status: 'ok',
        path: '/',
      },
      network: { speed: '', status: 'idle' },
      timestamp: 0,
    });
    await getSystemStatus();
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/system/status');
  });
});

describe('formatBytes', () => {
  it('returns 0 B for 0', () => {
    expect(formatBytes(0)).toBe('0 B');
  });

  it('uses 1 decimal place', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  it('scales up to GB', () => {
    expect(formatBytes(1024 ** 3)).toBe('1 GB');
  });
});

describe('getStorageDisplay', () => {
  it('returns Error on error/unknown status', () => {
    expect(
      getStorageDisplay({
        total_bytes: 0,
        used_bytes: 0,
        free_bytes: 0,
        percent_used: 0,
        status: 'error',
        path: '',
      }),
    ).toBe('Error');
    expect(
      getStorageDisplay({
        total_bytes: 0,
        used_bytes: 0,
        free_bytes: 0,
        percent_used: 0,
        status: 'unknown',
        path: '',
      }),
    ).toBe('Error');
  });

  it('returns free bytes on ok status', () => {
    expect(
      getStorageDisplay({
        total_bytes: 0,
        used_bytes: 0,
        free_bytes: 1024,
        percent_used: 0,
        status: 'ok',
        path: '',
      }),
    ).toBe('1 KB Free');
  });
});

describe('getQueueDisplay', () => {
  it('returns Offline on offline', () => {
    expect(
      getQueueDisplay({ active: 0, pending: 0, scheduled: 0, status: 'offline' }),
    ).toBe('Offline');
  });

  it('returns Outdated on outdated', () => {
    expect(
      getQueueDisplay({
        active: 0,
        pending: 0,
        scheduled: 0,
        status: 'outdated',
      }),
    ).toBe('Outdated');
  });

  it('returns Idle when active+pending=0', () => {
    expect(
      getQueueDisplay({
        active: 0,
        pending: 0,
        scheduled: 0,
        status: 'online',
      }),
    ).toBe('Idle');
  });

  it('returns N Active otherwise', () => {
    expect(
      getQueueDisplay({
        active: 3,
        pending: 2,
        scheduled: 0,
        status: 'online',
      }),
    ).toBe('3 Active');
  });
});
