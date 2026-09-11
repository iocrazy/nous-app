/**
 * Phase 2a: the answer channel's typed 4xx (`{detail: {code, message}}`) must
 * surface as IssueAnswerRejectedError so QuestionCard can map `code` to copy
 * instead of showing "409 Conflict: {...}".
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({}) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

import { IssueAnswerRejectedError, postIssueMessage } from './issueMessageService';

afterEach(() => vi.unstubAllGlobals());

function respond(status: number, body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), {
    status, statusText: status === 409 ? 'Conflict' : 'Bad Request', headers: { 'Content-Type': 'application/json' },
  })));
}

describe('postIssueMessage — typed answer rejections', () => {
  it('turns {detail:{code,message}} into IssueAnswerRejectedError', async () => {
    respond(409, { detail: { code: 'budget_still_exhausted', message: 'still spent' } });
    const err = await postIssueMessage(7, { body: 'Top up', answer_to: 'budget:42' }).catch((e) => e);
    expect(err).toBeInstanceOf(IssueAnswerRejectedError);
    expect(err.code).toBe('budget_still_exhausted');
    expect(err.status).toBe(409);
    expect(err.message).toBe('still spent');
  });

  it('keeps the generic error for an untyped body', async () => {
    respond(400, { detail: 'plain string detail' });
    const err = await postIssueMessage(7, { body: 'x' }).catch((e) => e);
    expect(err).not.toBeInstanceOf(IssueAnswerRejectedError);
    expect(String(err.message)).toContain('400');
  });
});

/**
 * The PRODUCTION envelope (CLAUDE.md 2026-09-09).
 *
 * `app/core/exceptions.py` wraps every `HTTPException` into
 * `{success, error, code:"http_<status>", request_id, details}` and the typed
 * code lands under `details` — `detail` does not survive the wrap at all. A
 * parser that reads only `detail` stays green against every hand-written
 * FastAPI-shaped fixture and degrades every real refusal on cn.nous.ink into
 * "400 Bad Request: {…}". These bodies are the real shape, copied from the
 * handler, which is why the untyped control below keeps the FastAPI shape: the
 * two must not be the same fixture.
 */
describe('postIssueMessage — the ErrorResponse envelope production actually sends', () => {
  const envelope = (code: string, message: string) => ({
    success: false,
    error: 'Request failed',
    code: 'http_400',
    request_id: 'req-1',
    details: { code, message },
  });

  it('reads the typed code out of details, not detail', async () => {
    respond(400, envelope('output_ref_unresolvable', "script_shot/9 v2 was not produced on this issue"));
    const err = await postIssueMessage(7, { body: 'look at this' }).catch((e) => e);
    expect(err).toBeInstanceOf(IssueAnswerRejectedError);
    expect(err.code).toBe('output_ref_unresolvable');
    expect(err.status).toBe(400);
  });

  it('reads the citation limit refusal the same way', async () => {
    respond(400, envelope('output_ref_limit_exceeded', '9 referenced outputs in one comment'));
    const err = await postIssueMessage(7, { body: 'x' }).catch((e) => e);
    expect(err.code).toBe('output_ref_limit_exceeded');
  });

  it('still ignores the envelope code, which names only the status', async () => {
    // `code: "http_400"` is on EVERY refusal. Reading it would make every
    // refusal look typed while telling the user nothing.
    respond(400, { success: false, error: 'Request failed', code: 'http_400', request_id: 'r', details: null });
    const err = await postIssueMessage(7, { body: 'x' }).catch((e) => e);
    expect(err).not.toBeInstanceOf(IssueAnswerRejectedError);
  });
});
