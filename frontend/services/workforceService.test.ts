/**
 * Unit tests for workforceService — focused on the new
 * ``getTaskByInbox`` lookup that backs the chat sub-task cards' live
 * Delegate status. Covers URL shape, error surface, and the response
 * envelope.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  workforceService,
  TERMINAL_LIFECYCLES,
} from './workforceService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

function stubFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
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

describe('workforceService.getTaskByInbox', () => {
  it('GETs /workforce/tasks/by-inbox/:id and returns the envelope', async () => {
    const body = {
      inbox_message_id: 'inbox-1',
      task: {
        id: 't1',
        agent_id: 'a1',
        lifecycle_status: 'done',
        started_at: null,
        ended_at: null,
        error_code: null,
        error_message: null,
        created_at: '2026-04-26',
        inbox_message_id: 'inbox-1',
        result: { ok: true },
      },
      outbox_response: null,
    };
    const spy = stubFetch(body);

    const out = await workforceService.getTaskByInbox('inbox-1');

    expect(out).toEqual(body);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('/workforce/tasks/by-inbox/inbox-1');
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method ?? 'GET').toBe('GET');
  });

  it('encodes path segment so weird inbox IDs do not break the URL', async () => {
    stubFetch({ inbox_message_id: 'x', task: null, outbox_response: null });
    const spy = vi.mocked(globalThis.fetch);
    await workforceService.getTaskByInbox('a/b c');
    expect(spy.mock.calls[0][0]).toContain('/by-inbox/a%2Fb%20c');
  });

  it('throws on 403 with the response body as message context', async () => {
    stubFetch({ detail: 'not authorized' }, 403);
    await expect(workforceService.getTaskByInbox('x')).rejects.toThrow(/403/);
  });
});

describe('TERMINAL_LIFECYCLES', () => {
  it('includes done / failed / cancelled — and only those', () => {
    expect(TERMINAL_LIFECYCLES.has('done')).toBe(true);
    expect(TERMINAL_LIFECYCLES.has('failed')).toBe(true);
    expect(TERMINAL_LIFECYCLES.has('cancelled')).toBe(true);
    // In-flight states must NOT be terminal — that's the contract the
    // realtime hook depends on for "still polling vs done".
    expect(TERMINAL_LIFECYCLES.has('queued')).toBe(false);
    expect(TERMINAL_LIFECYCLES.has('in_progress')).toBe(false);
    expect(TERMINAL_LIFECYCLES.has('assigned')).toBe(false);
    expect(TERMINAL_LIFECYCLES.has('waiting_for_other')).toBe(false);
    expect(TERMINAL_LIFECYCLES.has('blocked')).toBe(false);
  });
});
