import { beforeEach, describe, expect, it, vi } from 'vitest';

import { listInbox, markAllInboxRead, markInboxRead } from './notificationsService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer t' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

function stubFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    json: async () => body,
  } as unknown as Response);
}

describe('notificationsService', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listInbox hits /api/v1/inbox and returns the payload', async () => {
    const payload = {
      notifications: [{ id: '1', kind: 'generation_result', title: 'x', severity: 'success', read: false }],
      total: 1,
      unread_count: 1,
    };
    const spy = stubFetch(payload);
    const result = await listInbox();
    expect(result.unread_count).toBe(1);
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/inbox');
  });

  it('listInbox forwards unread_only / limit / offset query params', async () => {
    const spy = stubFetch({ notifications: [], total: 0, unread_count: 0 });
    await listInbox({ unreadOnly: true, limit: 10, offset: 20 });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('unread_only=true');
    expect(url).toContain('limit=10');
    expect(url).toContain('offset=20');
  });

  it('markInboxRead POSTs to the read endpoint with an encoded id', async () => {
    const spy = stubFetch({ success: true, message: 'ok' });
    await markInboxRead('99');
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/inbox/99/read');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('markAllInboxRead POSTs to /read-all', async () => {
    const spy = stubFetch({ success: true, message: 'ok' });
    await markAllInboxRead();
    expect(spy.mock.calls[0][0]).toBe('https://api.test/api/v1/inbox/read-all');
  });

  it('throws on a non-ok response', async () => {
    stubFetch({}, 500);
    await expect(listInbox()).rejects.toThrow(/Failed to load inbox/);
  });
});
