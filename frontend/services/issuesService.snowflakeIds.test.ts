/**
 * B3 — the issue-control calls take the id as a STRING and put it in the URL
 * untouched.
 *
 * Every id here is 2^53+1 (`9007199254740993`), the first Snowflake a JS
 * number cannot hold: round-tripping it through `Number()` silently yields
 * `…992` and the request goes to a DIFFERENT ISSUE. Nothing fails, nothing
 * logs — which is why this is a test and not a comment.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

import { pauseIssue, resumeIssue, updateIssue } from './issuesService';

/** 2^53+1 — `Number('9007199254740993')` is 9007199254740992. */
const BIG = '9007199254740993';

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const calledUrl = (): string => fetchMock.mock.calls[0][0] as string;

describe('issue ids past 2^53 reach the URL intact', () => {
  it('pauseIssue', async () => {
    await pauseIssue(BIG);
    expect(calledUrl()).toBe(`http://api.test/api/v1/issues/${BIG}/pause`);
    expect(calledUrl()).not.toContain('9007199254740992');
  });

  it('resumeIssue', async () => {
    await resumeIssue(BIG);
    expect(calledUrl()).toBe(`http://api.test/api/v1/issues/${BIG}/resume`);
  });

  it('updateIssue', async () => {
    await updateIssue(BIG, { budget_cents: 500 });
    expect(calledUrl()).toBe(`http://api.test/api/v1/issues/${BIG}`);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ budget_cents: 500 });
  });
});
