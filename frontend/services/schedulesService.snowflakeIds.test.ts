/**
 * B3 — the two issue-scoped schedule calls take the id as a STRING, and the
 * two of them do DIFFERENT things with it on purpose:
 *
 * - `listIssueSchedules` puts it in the URL path, where the string survives;
 * - `createIssueWakeup` puts it in a JSON body, where the wire shape is a
 *   NUMBER (`payload->>'issue_id'` on the backend reads what the producer
 *   wrote, and every other `/issues/*` payload writes a number). That
 *   `Number()` is lossy past 2^53 — the test below says so out loud rather
 *   than leaving it to be discovered. Fixing it means changing the wire on
 *   both sides, which a refactor does not get to do (CLAUDE.md 边界 mock
 *   必须用真实 JSON 形状).
 *
 * The id is 2^53+1, the first Snowflake a JS number cannot hold.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

import { createIssueWakeup, listIssueSchedules } from './schedulesService';

const BIG = '9007199254740993';

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'content-type': 'application/json' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('schedule calls and Snowflake issue ids', () => {
  it('listIssueSchedules keeps the id intact in the path', async () => {
    await listIssueSchedules(BIG);
    expect(fetchMock.mock.calls[0][0]).toBe(`http://api.test/api/v1/issues/${BIG}/schedules`);
  });

  it('createIssueWakeup converts at the boundary, because the wire is a number', async () => {
    await createIssueWakeup(BIG, { fireAt: '2026-09-15T10:00:00Z', text: 'ping' });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const payload = JSON.parse(init.body as string).payload as Record<string, unknown>;
    expect(typeof payload.issue_id).toBe('number');
    // The documented cost of that wire shape, pinned where it happens: this
    // is the only place in the chain that can lose a digit, and a caller
    // reading the service can see it.
    expect(payload.issue_id).toBe(9007199254740992);
  });
});
