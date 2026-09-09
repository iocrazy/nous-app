/**
 * harness 2b-1 §2 — forkRun rejects with a typed RunForkRejectedError carrying
 * the server's detail.code; 201 passes the body through. Bodies are the real
 * FastAPI shapes the fork endpoint emits.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

const { aiLibraryService, RunForkRejectedError } = await import('./aiLibraryService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('aiLibraryService.forkRun', () => {
  it('posts at_seq + steer and passes a 201 body through', async () => {
    const created = { run_id: null, session_id: '200', workflow_id: 'wf-1', issue_id: 9, forked_from: { run_id: 42, at_seq: 4 } };
    fetchMock.mockResolvedValueOnce(json(201, created));
    const out = await aiLibraryService.forkRun('42', { at_seq: 4, steer: 'be darker' });
    expect(out).toEqual(created);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/ai-library/runs/42/fork');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ at_seq: 4, steer: 'be darker' });
  });

  it('maps {detail:{code,message}} to RunForkRejectedError.code', async () => {
    fetchMock.mockResolvedValueOnce(json(409, { detail: { code: 'run_live', message: 'pause or cancel the running run first' } }));
    const err = await aiLibraryService.forkRun('42', { at_seq: 4 }).catch((e) => e);
    expect(err).toBeInstanceOf(RunForkRejectedError);
    expect(err.code).toBe('run_live');
    expect(err.status).toBe(409);
    expect(err.message).toBe('pause or cancel the running run first');
  });

  it('reads the PRODUCTION envelope: {error, code: http_409, details: {code, message}}', async () => {
    // Verbatim shape from cn.nous.ink on 2026-09-09 (app/core/exceptions.py ErrorResponse)
    fetchMock.mockResolvedValueOnce(
      json(409, {
        success: false,
        error: 'Request failed',
        code: 'http_409',
        request_id: '048e5f13-65f1-4d38-8461-bf3808dc7029',
        details: { code: 'not_an_issue_run', message: 'only issue runs can be forked' },
      }),
    );
    const err = await aiLibraryService.forkRun('42', { at_seq: 3 }).catch((e) => e);
    expect(err).toBeInstanceOf(RunForkRejectedError);
    expect(err.code).toBe('not_an_issue_run');
    expect(err.message).toBe('only issue runs can be forked');
  });

  it('an envelope with a string error and null details keeps http_<status> + the error text', async () => {
    fetchMock.mockResolvedValueOnce(json(404, { success: false, error: 'run not found', code: 'http_404', request_id: 'x', details: null }));
    const err = await aiLibraryService.forkRun('x', { at_seq: 1 }).catch((e) => e);
    expect(err.code).toBe('http_404');
    expect(err.message).toBe('run not found');
  });

  it('a plain-string detail becomes http_<status>', async () => {
    fetchMock.mockResolvedValueOnce(json(404, { detail: 'run not found' }));
    const err = await aiLibraryService.forkRun('x', { at_seq: 1 }).catch((e) => e);
    expect(err.code).toBe('http_404');
    expect(err.message).toBe('run not found');
  });
});

describe('aiLibraryService.getRunForks', () => {
  it('reads the fork list', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [{ run_id: '77', at_seq: 4, created_at: 't', status: 'completed' }] }));
    const out = await aiLibraryService.getRunForks('42');
    expect(out.items[0].run_id).toBe('77');
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/ai-library/runs/42/forks');
  });
});
