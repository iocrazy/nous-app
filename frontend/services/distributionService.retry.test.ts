/**
 * The retry call's two contracts with the backend.
 *
 * 1. `mode` goes on the wire. The endpoint distinguishes "run the same thing
 *    again" from "drop the schedule and go now", and only the second may
 *    change what the user originally asked for.
 * 2. `isScheduleUnreachable` recognises the typed 409, so the page can say why
 *    instead of falling back to a generic failure toast.
 *
 * Both stub `fetch` rather than the service, so the request really is built and
 * the error envelope really is parsed.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({
    'Content-Type': 'application/json',
    Authorization: 'Bearer test',
  }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { isScheduleUnreachable, retryPublishTask } from './distributionService';

afterEach(() => { vi.unstubAllGlobals(); });

const okFetch = () =>
  vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: '801' }) });

/** The real 409 envelope: `{"detail": {"reason": ..., "message": ...}}`. */
const conflictFetch = (reason: string) =>
  vi.fn().mockResolvedValue({
    ok: false,
    status: 409,
    json: async () => ({ detail: { reason, message: 'written for the logs' } }),
  });

describe('retryPublishTask', () => {
  it('defaults to the mode that cannot change the user\'s intent', async () => {
    const spy = okFetch();
    vi.stubGlobal('fetch', spy);

    await retryPublishTask('801');

    const init = spy.mock.calls[0][1] as RequestInit;
    // Explicit on the wire rather than relying on the server default: this is
    // the value that means "do NOT touch my schedule".
    expect(JSON.parse(String(init.body))).toEqual({ mode: 'as_scheduled' });
  });

  it('sends mode=now only when asked', async () => {
    const spy = okFetch();
    vi.stubGlobal('fetch', spy);

    await retryPublishTask('801', 'now');

    const init = spy.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ mode: 'now' });
  });
});

describe('isScheduleUnreachable', () => {
  it('recognises the typed 409 so the page can say why', async () => {
    vi.stubGlobal('fetch', conflictFetch('schedule_unreachable'));

    await expect(retryPublishTask('801')).rejects.toSatisfy(isScheduleUnreachable);
  });

  it('does not claim an unrelated 409 is a schedule problem', async () => {
    // `Task is not in a retryable state` is also a 409 — and its detail is a
    // bare string, not an envelope. Treating it as "your schedule expired"
    // would print a confident, wrong reason and point the user at a button
    // that solves nothing.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Task is not in a retryable state' }),
    }));

    await expect(retryPublishTask('801')).rejects.not.toSatisfy(isScheduleUnreachable);
  });

  it('does not fire on a 409 carrying some other reason', async () => {
    vi.stubGlobal('fetch', conflictFetch('something_else'));

    await expect(retryPublishTask('801')).rejects.not.toSatisfy(isScheduleUnreachable);
  });

  it('ignores non-errors and other statuses', () => {
    expect(isScheduleUnreachable(new Error('network down'))).toBe(false);
    expect(isScheduleUnreachable(null)).toBe(false);
    expect(isScheduleUnreachable({ status: 409, detail: { reason: 'schedule_unreachable' } }))
      .toBe(false);
  });
});
