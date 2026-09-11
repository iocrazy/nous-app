/**
 * A refused CITATION must reach the writer as a sentence about citations.
 *
 * The two codes come from `output_ref_resolver` (`UNRESOLVABLE` /
 * `LIMIT_EXCEEDED`), and the refusal is all-or-nothing by design: the whole
 * comment is rejected rather than posted with one bad reference, so the copy
 * has to say the comment did not go. The server's own prose is never shown —
 * it names a kind and a snowflake ("script_shot/727… v2 was not produced on
 * this issue"), which is a sentence for a log.
 */
import { describe, expect, it } from 'vitest';

import { IssueAnswerRejectedError } from '../../services/issueMessageService';
import { outputRefErrorText, isOutputRefRejection, replyErrorText } from './outputRefErrors';

/** The real `t` contract used across this folder: key, fallback, vars. */
const t = (_key: string, fallback: string, vars?: Record<string, unknown>): string =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));

const reject = (code: string) =>
  new IssueAnswerRejectedError(400, code, 'script_shot/727145299382534999 v2 was not produced on this issue');

describe('outputRefErrorText', () => {
  it('says the reference could not be resolved, not that the server failed', () => {
    const text = outputRefErrorText(reject('output_ref_unresolvable'), t);
    expect(text).toMatch(/referenc|cited/i);
    // The server's prose carries an internal kind and a snowflake.
    expect(text).not.toContain('727145299382534999');
    expect(text).not.toContain('script_shot');
  });

  it('names the cap in the limit sentence by interpolating the constant', async () => {
    const { MAX_OUTPUT_REF_ATTACHMENTS } = await import('../chat/attachmentLimits');
    const text = outputRefErrorText(reject('output_ref_limit_exceeded'), t);
    expect(text).toContain(String(MAX_OUTPUT_REF_ATTACHMENTS));
  });

  it('names an unknown code rather than inventing a reason for it', () => {
    expect(outputRefErrorText(reject('output_ref_some_future_code'), t)).toContain(
      'output_ref_some_future_code',
    );
  });

  it('isOutputRefRejection claims only the citation codes', () => {
    expect(isOutputRefRejection(reject('output_ref_unresolvable'))).toBe(true);
    expect(isOutputRefRejection(reject('output_ref_limit_exceeded'))).toBe(true);
    // Another kind's refusal must fall through to its own handler — claiming
    // it here would answer a budget question with a sentence about citations.
    expect(isOutputRefRejection(reject('budget_still_exhausted'))).toBe(false);
    expect(isOutputRefRejection(new Error('network down'))).toBe(false);
  });
});

/**
 * What the reply composer's catch actually shows.
 *
 * Its own function rather than a ternary inside `IssueDetailView.handleReply`
 * because the branch is the whole point: a citation refusal must NOT fall
 * through to `err.message`, which on this path is the server's internal
 * sentence ("script_shot/727… v2 was not produced on this issue"), and every
 * other failure must NOT be dressed up as a citation problem.
 */
describe('replyErrorText', () => {
  it('uses the citation copy for a citation refusal', () => {
    const text = replyErrorText(reject('output_ref_unresolvable'), t);
    expect(text).toMatch(/referenc|cited/i);
    expect(text).not.toContain('script_shot');
  });

  it('leaves another kind’s typed refusal to its own message', () => {
    // A parked question's refusal already carries copy `QuestionCard` maps;
    // answering it with a sentence about citations would be worse than the
    // server's own words.
    expect(replyErrorText(reject('no_open_question'), t)).not.toMatch(/referenc/i);
  });

  it('falls back to the error’s own message for an ordinary failure', () => {
    expect(replyErrorText(new Error('Network request failed'), t)).toBe('Network request failed');
  });

  it('has something to say about a thrown non-Error', () => {
    expect(replyErrorText('boom', t)).toBeTruthy();
  });
});
