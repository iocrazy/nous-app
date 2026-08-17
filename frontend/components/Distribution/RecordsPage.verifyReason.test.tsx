/**
 * A read-back reason must be read off the code the detail LEADS with, never off
 * a code that merely appears somewhere inside it.
 *
 * Why this file exists
 * ====================
 * `verify_detail` is `[code] prose`, and the bracketed code is the contract
 * (`types.ts` says so on the field). But two of the verdicts legitimately
 * embed a SECOND code, because giving up has to say what it gave up on:
 *
 *   [verification_abandoned] gave up after 5 attempt(s); last failure: [x] ...
 *   [verification_abandoned] no conclusive read-back after 5 attempts — ...;
 *     last attempt: [list_unreadable] ... [probe ...] [page ...] [render ...]
 *
 * The matcher used to scan the whole string, so whichever known code appeared
 * first anywhere won. Feed it an abandoned row whose final attempt mentioned
 * `[not_found]` and the page printed:
 *
 *   "This post is not in the account any more — it may have been removed."
 *
 * That is the single worst sentence this page can produce, and it is FALSE by
 * construction: `abandoned` means we never managed to see the list. It is the
 * same false verdict the read-back itself was fixed to stop emitting
 * (`list_not_ready` → inconclusive rather than `not_live`) — leaking back in
 * one layer up, through the copy.
 *
 * Same family as this repo's 「允许」/「不允许」 rule: match the token, not a
 * fragment of a longer string that happens to contain it.
 */

import { describe, expect, it } from 'vitest';

import { verifyReasonEntry } from './RecordsPage';

describe('verifyReasonEntry', () => {
  it('reads the code the detail leads with', () => {
    expect(verifyReasonEntry('[rejected] the platform refused this post')?.key)
      .toBe('distribution.records.verifyRejected');
    expect(verifyReasonEntry('[under_review] still reviewing')?.key)
      .toBe('distribution.records.verifyUnderReview');
    expect(verifyReasonEntry('[not_found] read 12 works and none matches')?.key)
      .toBe('distribution.records.verifyNotFound');
  });

  it('tolerates leading whitespace and case', () => {
    expect(verifyReasonEntry('  [REJECTED] shouty')?.key)
      .toBe('distribution.records.verifyRejected');
  });

  it('returns null for a code it does not know, rather than guessing', () => {
    expect(verifyReasonEntry('[list_not_ready] the list had not rendered')).toBeNull();
    expect(verifyReasonEntry('[verification_abandoned] gave up')).toBeNull();
  });

  it('returns null for junk, empty and missing details', () => {
    for (const raw of [null, undefined, '', '   ', 'no brackets here', '] [']) {
      expect(verifyReasonEntry(raw)).toBeNull();
    }
  });

  /**
   * **The regression this file is named after.**
   *
   * An abandoned verdict now carries the last attempt's diagnosis so that
   * giving up says why. That diagnosis is another bracketed code, and it must
   * not be allowed to decide the copy.
   */
  it('never lets an inner code speak for an abandoned row', () => {
    const abandoned = [
      '[verification_abandoned] no conclusive read-back after 5 attempts — we '
      + 'could not SEE this post, which is not the same as it not being there; '
      + 'last attempt: [not_found] read 12 work(s) and none matches the title',
      '[verification_abandoned] gave up after 5 attempt(s); last failure: '
      + '[rejected] the platform refused this post',
      '[verification_abandoned] gave up after 5 attempt(s); last failure: '
      + '[under_review] the platform is still reviewing this post',
    ];
    for (const raw of abandoned) {
      // Falls through to `verifyUnconfirmedGeneric` at the call site, which is
      // the only honest reading: we did not find out.
      expect(verifyReasonEntry(raw)).toBeNull();
    }
  });

  it('still reads a genuine not_found that leads the line', () => {
    // The guard above must not have cost us the real signal: when the platform
    // really did tell us the post is gone, that verdict leads the detail.
    expect(verifyReasonEntry('[not_found] the creator centre reports this '
      + 'account has no works at all')?.key)
      .toBe('distribution.records.verifyNotFound');
  });
});
