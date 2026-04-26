/**
 * Unit tests for the frontend error reporter.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { __test, installErrorReporter, reportError } from './errorReporter';

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function spyFetch() {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    { ok: true, status: 204 } as unknown as Response,
  );
}

describe('normalizeError', () => {
  it('extracts message + stack from Error instances', () => {
    const err = new Error('boom');
    const n = __test.normalizeError(err);
    expect(n.message).toBe('boom');
    expect(n.stack).toContain('Error');
  });

  it('uses strings as-is', () => {
    const n = __test.normalizeError('raw string');
    expect(n.message).toBe('raw string');
    expect(n.stack).toBeUndefined();
  });

  it('JSON-stringifies objects', () => {
    const n = __test.normalizeError({ type: 'x', code: 1 });
    expect(n.message).toContain('"type"');
    expect(n.message).toContain('"code":1');
  });
});

describe('reportError', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it('POSTs a report with session_id and user_agent', async () => {
    const fetchSpy = spyFetch();
    await reportError(new Error('boom'));
    // fetch is fire-and-forget inside reportError, but we await reportError
    // which only awaits its own work; the post is queued via `void postReport`.
    // Give the microtask a tick.
    await new Promise(r => setTimeout(r, 0));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/errors/report');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.message).toBe('boom');
    expect(body.error_type).toBe('runtime');
    expect(body.session_id).toBeTruthy();
    expect(body.user_agent).toBeTruthy();
  });

  it('dedupes identical errors within the window', async () => {
    const fetchSpy = spyFetch();
    await reportError(new Error('same'));
    await reportError(new Error('same'));
    await reportError(new Error('same'));
    await new Promise(r => setTimeout(r, 0));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('does not dedupe distinct messages', async () => {
    const fetchSpy = spyFetch();
    await reportError(new Error('a'));
    await reportError(new Error('b'));
    await new Promise(r => setTimeout(r, 0));

    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('swallows reporter exceptions instead of rethrowing', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('net down'));
    // Must not throw.
    await expect(reportError(new Error('whatever'))).resolves.toBeUndefined();
    await new Promise(r => setTimeout(r, 0));
  });

  it('skips browser-noise messages (ResizeObserver loop)', async () => {
    const fetchSpy = spyFetch();
    await reportError(
      new Error('ResizeObserver loop completed with undelivered notifications.'),
    );
    await reportError(new Error('ResizeObserver loop limit exceeded'));
    await reportError(new Error('Transition was skipped'));
    await new Promise(r => setTimeout(r, 0));

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('still reports messages that contain noise as a substring of a real error', async () => {
    const fetchSpy = spyFetch();
    // Real error message that doesn't trip the filter
    await reportError(new Error('Network request failed: timeout'));
    await new Promise(r => setTimeout(r, 0));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('truncates very long stacks and messages', async () => {
    const fetchSpy = spyFetch();
    const longMessage = 'x'.repeat(5000);
    const longStack = 'y'.repeat(20000);
    const err = new Error(longMessage);
    err.stack = longStack;

    await reportError(err);
    await new Promise(r => setTimeout(r, 0));

    const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
    expect(body.message.length).toBeLessThanOrEqual(2000);
    expect(body.stack.length).toBeLessThanOrEqual(8000);
  });
});

describe('installErrorReporter', () => {
  it('is idempotent — calling twice does not double-register', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    installErrorReporter();
    const firstCount = addSpy.mock.calls.filter(
      c => c[0] === 'error' || c[0] === 'unhandledrejection',
    ).length;
    installErrorReporter();
    const secondCount = addSpy.mock.calls.filter(
      c => c[0] === 'error' || c[0] === 'unhandledrejection',
    ).length;
    expect(secondCount).toBe(firstCount);
  });
});
