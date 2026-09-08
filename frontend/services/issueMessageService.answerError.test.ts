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
