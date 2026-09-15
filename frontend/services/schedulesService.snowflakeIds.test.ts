/**
 * B3 — the two issue-scoped schedule calls take the id as a STRING.
 *
 * - `listIssueSchedules` puts it in the URL path, where the string survives.
 *   That is the CONTRACT, and the test below is a guard on it.
 * - `createIssueWakeup` puts it in a JSON body and converts to a number
 *   first, which loses a digit past 2^53. That is NOT a contract — it is a
 *   BUG this refactor deliberately did not touch, because changing a wire
 *   byte is outside a refactor's remit. The backend would take the string
 *   losslessly (`schedules_router` does `body["issue_id"] = int(issue_id)`
 *   at the edge). Tracked as a fix ticket: frontend-convergence-report.md
 *   记票 §F1.
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

  // ⚠️ NOT a contract test. It pins the CURRENT, KNOWN-LOSSY behaviour so
  // that the digit this chain drops is visible in one named place instead of
  // being rediscovered from a wake-up that fired on the wrong issue. The fix
  // (send the string; the backend's `int()` takes it losslessly) is its own
  // ticket — 记票 §F1 — and when it lands, this test should be REWRITTEN to
  // assert the string, not deleted quietly.
  it('createIssueWakeup still converts to a number, losing a digit past 2^53 (known bug, §F1)', async () => {
    await createIssueWakeup(BIG, { fireAt: '2026-09-15T10:00:00Z', text: 'ping' });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const payload = JSON.parse(init.body as string).payload as Record<string, unknown>;
    expect(typeof payload.issue_id).toBe('number');
    expect(payload.issue_id).toBe(9007199254740992);
    // Said out loud: the id that left this function is NOT the id it was given.
    expect(String(payload.issue_id)).not.toBe(BIG);
  });
});
