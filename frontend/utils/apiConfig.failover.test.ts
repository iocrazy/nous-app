// 双通道故障转移:cn 直连(家宽,快但会抖)断线时切 Cloudflare 通道,
// primary 恢复后切回。2026-08-26 用户连续两次撞上 ERR_CONNECTION_CLOSED
// 白屏 —— 单点直连没有兜底是结构缺陷,不是运维事故。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  _resetFailoverForTests,
  getApiUrl,
  getFallbackApiUrl,
  probePrimaryOnce,
  reportApiNetworkFailure,
} from './apiConfig';

describe('api base failover', () => {
  beforeEach(() => {
    _resetFailoverForTests();
    vi.stubEnv('VITE_API_URL', 'https://cn.nous.ink:88');
    vi.stubEnv('VITE_API_FALLBACK_URL', 'https://api.nous.ink');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('stays on primary until failures accumulate', () => {
    expect(getApiUrl()).toBe('https://cn.nous.ink:88');
    reportApiNetworkFailure();
    expect(getApiUrl()).toBe('https://cn.nous.ink:88');
  });

  it('switches to the fallback after repeated network failures', () => {
    reportApiNetworkFailure();
    reportApiNetworkFailure();
    expect(getApiUrl()).toBe('https://api.nous.ink');
  });

  it('probe flips back to primary once it answers again', async () => {
    reportApiNetworkFailure();
    reportApiNetworkFailure();
    expect(getApiUrl()).toBe('https://api.nous.ink');
    const ok = vi.fn().mockResolvedValue({ ok: true } as Response);
    vi.stubGlobal('fetch', ok);
    await probePrimaryOnce();
    expect(getApiUrl()).toBe('https://cn.nous.ink:88');
    expect(ok).toHaveBeenCalledWith(
      'https://cn.nous.ink:88/api/v1/healthz',
      expect.anything(),
    );
  });

  it('probe keeps the fallback while primary is still dead', async () => {
    reportApiNetworkFailure();
    reportApiNetworkFailure();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('down')));
    await probePrimaryOnce();
    expect(getApiUrl()).toBe('https://api.nous.ink');
  });

  it('no fallback configured → failover never engages', () => {
    vi.stubEnv('VITE_API_FALLBACK_URL', '');
    _resetFailoverForTests();
    reportApiNetworkFailure();
    reportApiNetworkFailure();
    expect(getFallbackApiUrl()).toBeNull();
    expect(getApiUrl()).toBe('https://cn.nous.ink:88');
  });
});
