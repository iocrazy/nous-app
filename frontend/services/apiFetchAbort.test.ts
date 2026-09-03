/**
 * A caller's own abort is not evidence that the server is down.
 *
 * Both fetch seams — `envelopeFetch` (/assets, /generated) and `apiFetch`
 * (everything on `apiClient`) — feed `reportApiNetworkFailure`, which drives
 * the dual-channel failover: `FAILOVER_THRESHOLD` is **2**, `_failureCount`
 * never resets on success, and `VITE_API_FALLBACK_URL` is set in production
 * (`frontend/.env.production`, `deploy-pages.yml`). So two miscounted aborts
 * move EVERY subsequent request in the app onto the Cloudflare tunnel until a
 * 60s probe flips it back.
 *
 * That became reachable when `searchAssetsAccessible` started passing an
 * `AbortSignal` — the chat `@` picker aborts its superseded search on every
 * keystroke, so "type three characters in the Assets tab" was two aborts and a
 * failover. The guard belongs at the seam, not in the picker: any future caller
 * that cancels gets it for free.
 *
 * MUTATION NOTE — delete `if (isCallerAbort(err)) throw err;` from either
 * `services/apiEnvelope.ts` or `services/apiClient.ts` and the matching
 * "does not count" case here goes red.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const reportApiNetworkFailure = vi.fn();

vi.mock('../utils/apiConfig', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../utils/apiConfig')>();
  return {
    ...mod,
    // `isCallerAbort` is the thing under test — keep the real one.
    reportApiNetworkFailure: () => reportApiNetworkFailure(),
    getApiUrl: () => 'https://api.example.test',
    getFallbackApiUrl: () => 'https://fallback.example.test',
  };
});

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer e2e' }),
}));

import { envelopeFetch, GeneratedApiError } from './apiEnvelope';
import { isCallerAbort } from '../utils/apiConfig';

/** What `fetch` rejects with when a signal aborts: a DOMException named
 *  `AbortError`, NOT a plain `Error`. */
function abortError(): unknown {
  return typeof DOMException !== 'undefined'
    ? new DOMException('The operation was aborted.', 'AbortError')
    : Object.assign(new Error('aborted'), { name: 'AbortError' });
}

/** What `AbortSignal.timeout()` rejects with. A timeout IS a network signal
 *  and must keep counting — the two are told apart by name, not by
 *  `signal.aborted`, which is true for both. */
function timeoutError(): unknown {
  return typeof DOMException !== 'undefined'
    ? new DOMException('The operation timed out.', 'TimeoutError')
    : Object.assign(new Error('timed out'), { name: 'TimeoutError' });
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('isCallerAbort', () => {
  it('matches an AbortError and nothing else', () => {
    expect(isCallerAbort(abortError())).toBe(true);
    // A timeout leaves `signal.aborted` true as well, which is exactly why
    // this is keyed on the name: a timeout is the origin failing to answer.
    expect(isCallerAbort(timeoutError())).toBe(false);
    expect(isCallerAbort(new TypeError('Failed to fetch'))).toBe(false);
    expect(isCallerAbort(null)).toBe(false);
    expect(isCallerAbort('AbortError')).toBe(false);
  });
});

describe('envelopeFetch', () => {
  it('does not count a caller-initiated abort as a network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abortError()));
    const controller = new AbortController();
    controller.abort();

    await expect(
      envelopeFetch('https://api.example.test/api/v1/assets/search', {
        signal: controller.signal,
      }),
    ).rejects.toSatisfy((err: unknown) => isCallerAbort(err));

    expect(reportApiNetworkFailure).not.toHaveBeenCalled();
  });

  it('rethrows the abort untouched, so a caller can tell it from a dead origin', async () => {
    // Wrapped in a `GeneratedApiError` the two are indistinguishable, and a
    // picker would paint "could not load the library" over a search the user
    // themselves superseded.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abortError()));
    const err = await envelopeFetch('https://api.example.test/api/v1/assets/search', {
      signal: new AbortController().signal,
    }).catch((e: unknown) => e);

    expect((err as { name: string }).name).toBe('AbortError');
    expect(err).not.toBeInstanceOf(GeneratedApiError);
  });

  it('still counts a real network failure', async () => {
    // The negative control. Without it, a guard that swallowed EVERY rejection
    // would pass the two cases above and silently disable the failover.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(
      envelopeFetch('https://api.example.test/api/v1/assets/search'),
    ).rejects.toBeInstanceOf(GeneratedApiError);

    expect(reportApiNetworkFailure).toHaveBeenCalledTimes(1);
  });

  it('still counts a timeout', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(timeoutError()));

    await expect(
      envelopeFetch('https://api.example.test/api/v1/assets/search'),
    ).rejects.toBeInstanceOf(GeneratedApiError);

    expect(reportApiNetworkFailure).toHaveBeenCalledTimes(1);
  });
});

describe('apiFetch', () => {
  it('does not count a caller-initiated abort, and does not retry it', async () => {
    // The retry is the worse half: re-issuing on the fallback base is exactly
    // the request the caller just asked us to stop.
    const fetchMock = vi.fn().mockRejectedValue(abortError());
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./apiClient');

    const controller = new AbortController();
    controller.abort();
    await expect(
      apiFetch('/api/v1/assets/search', { signal: controller.signal }),
    ).rejects.toSatisfy((err: unknown) => isCallerAbort(err));

    expect(reportApiNetworkFailure).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('still counts a real network failure', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./apiClient');

    await expect(apiFetch('/api/v1/assets/search')).rejects.toBeTruthy();
    expect(reportApiNetworkFailure).toHaveBeenCalledTimes(1);
  });
});
