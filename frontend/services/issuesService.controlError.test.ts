/**
 * Phase 2a §2 — pause / resume reject with a typed IssueControlError carrying
 * the server's detail.code, never the raw body. Bodies here are the real
 * FastAPI shapes the router emits.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

import { IssueControlError, listPaused, pauseIssue, resumeIssue } from './issuesService';

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('pauseIssue / resumeIssue — typed control errors', () => {
  it('maps a FastAPI {detail:{code,message}} 409 to IssueControlError.code', async () => {
    fetchMock.mockResolvedValueOnce(json(409, { detail: { code: 'already_paused', message: 'issue is already paused' } }));
    const err = await pauseIssue(5).catch((e) => e);
    expect(err).toBeInstanceOf(IssueControlError);
    expect(err.code).toBe('already_paused');
    expect(err.status).toBe(409);
    expect(err.message).toBe('issue is already paused');
    expect(err.message).not.toContain('{');
  });

  it('maps a plain-string detail to http_<status> with the detail as message', async () => {
    fetchMock.mockResolvedValueOnce(json(404, { detail: 'Issue not found' }));
    const err = await resumeIssue(5).catch((e) => e);
    expect(err).toBeInstanceOf(IssueControlError);
    expect(err.code).toBe('http_404');
    expect(err.message).toBe('Issue not found');
  });

  it('survives a non-JSON error body', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>502</html>', { status: 502, statusText: 'Bad Gateway' }));
    const err = await pauseIssue(5).catch((e) => e);
    expect(err).toBeInstanceOf(IssueControlError);
    expect(err.code).toBe('http_502');
  });

  it('returns the body on success', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { issue_id: '5', dispatched: true, reason: 'dispatched', workflow_id: 'wf', run_id: null }));
    await expect(resumeIssue(5)).resolves.toMatchObject({ reason: 'dispatched' });
  });
});

describe('listPaused', () => {
  it('normalises has_more to a boolean and items to an array', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [{ issue_id: '5', title: 't', paused_at: 'x', identifier: null, team_id: null, project_id: null, assignee_agent_id: null }], has_more: true }));
    const out = await listPaused();
    expect(out.has_more).toBe(true);
    expect(out.items).toHaveLength(1);
    fetchMock.mockResolvedValueOnce(json(200, { items: [] }));
    expect((await listPaused()).has_more).toBe(false);
  });
});
