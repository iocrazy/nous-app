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
 * "does not count" case here goes red. On the `apiClient` side BOTH of its
 * assertions go red, the retry one included: the mocked `getApiUrl` flips to
 * the fallback base when `reportApiNetworkFailure` fires, so removing the
 * guard reaches the retry and issues a second `fetch`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const PRIMARY = 'https://api.example.test';
const FALLBACK = 'https://fallback.example.test';

/** The base `getApiUrl()` answers with RIGHT NOW.
 *
 *  A constant here is what made the retry assertion below vacuous: `apiFetch`
 *  only re-issues when `buildUrl` produces a different URL the second time, so
 *  a frozen base meant the retry branch could never run and
 *  `toHaveBeenCalledTimes(1)` was true no matter what the guard did. The real
 *  module flips this base as a SIDE EFFECT of `reportApiNetworkFailure`
 *  crossing `FAILOVER_THRESHOLD`, so the mock flips it there too — one report
 *  is enough here, which is the point: it makes the retry reachable. */
let apiBase = PRIMARY;

const reportApiNetworkFailure = vi.fn(() => {
  apiBase = FALLBACK;
});

vi.mock('../utils/apiConfig', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../utils/apiConfig')>();
  return {
    ...mod,
    // `isCallerAbort` is the thing under test — keep the real one.
    reportApiNetworkFailure: () => reportApiNetworkFailure(),
    getApiUrl: () => apiBase,
    getFallbackApiUrl: () => FALLBACK,
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
  apiBase = PRIMARY;
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
  /** One aborted request, driven through the real `apiFetch`. */
  async function abortedTurn() {
    const fetchMock = vi.fn().mockRejectedValue(abortError());
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./apiClient');

    const controller = new AbortController();
    controller.abort();
    await expect(
      apiFetch('/api/v1/assets/search', { signal: controller.signal }),
    ).rejects.toSatisfy((err: unknown) => isCallerAbort(err));
    return fetchMock;
  }

  // Two separate cases, not two assertions in one: vitest stops a test at its
  // first failed expect, so pairing them would let the counting assertion mask
  // the retry one and leave the retry claim unproven.
  it('does not count a caller-initiated abort', async () => {
    await abortedTurn();
    expect(reportApiNetworkFailure).not.toHaveBeenCalled();
  });

  it('does not retry a caller-initiated abort', async () => {
    // The worse half: re-issuing on the fallback base is exactly the request
    // the caller just asked us to stop. Falsifiable because the mocked base
    // FLIPS on report (see `apiBase`) — drop the guard and this fetch count
    // becomes 2. The sibling test below shows the retry really firing once a
    // failure IS counted.
    const fetchMock = await abortedTurn();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('still counts a real network failure', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./apiClient');

    await expect(apiFetch('/api/v1/assets/search')).rejects.toBeTruthy();
    expect(reportApiNetworkFailure).toHaveBeenCalledTimes(1);
  });

  it('retries a counted failure on the fallback base', async () => {
    // The positive control for the assertion above, and the first coverage
    // this repo has of the fallback retry at all: without it "does not retry"
    // is a claim about a branch no test ever entered.
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./apiClient');

    const res = await apiFetch('/api/v1/assets/search');

    expect(res.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // Same path, different origin — the failover moved the base, the request
    // did not change.
    expect(fetchMock.mock.calls[0][0]).toBe(`${PRIMARY}/api/v1/assets/search`);
    expect(fetchMock.mock.calls[1][0]).toBe(`${FALLBACK}/api/v1/assets/search`);
  });
});
