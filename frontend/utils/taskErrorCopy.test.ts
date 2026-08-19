import { describe, it, expect } from 'vitest';
import { taskErrorCopy } from './taskErrorCopy';

describe('taskErrorCopy', () => {
  // The end-to-end path for the failure this whole change exists for, using
  // the values the FIXED backend produces: llm_fallback_chain folds the
  // provider body into the exception message, error_catalog classifies it as
  // PROVIDER_QUOTA_CAP (not the generic 429 code), migration 432 derives the
  // message into error_msg, and this is what the user finally reads.
  //
  // An earlier version of this test asserted `/rate-limiting/i` here — i.e.
  // it pinned the exact wrong behaviour as correct, because the backend
  // mapped every 429 to PROVIDER_RATE_LIMIT and the "account cap" copy this
  // change added could never be reached. Testing the pieces separately
  // (humanizeTaskError directly on one side, taskErrorCopy on the other) hid
  // the contradiction: both sides were green and the user still got
  // "wait a moment and retry" for a cap that never clears.
  it('shows the account-cap copy end to end for the 2026-08-19 ai_summary failure', () => {
    const copy = taskErrorCopy(
      { run_id: '340309387072606', error_code: 'PROVIDER_QUOTA_CAP' },
      'DBOSMaxStepRetriesExceeded: all 1 model(s) failed: ' +
        'doubao-seed-2-0-pro-260215 (HTTPStatusError HTTP 429: ' +
        '{"error":{"code":"SetLimitExceeded","message":"Your account has ' +
        'reached the set inference limit for the [doubao-seed-2-0-pro] model"}})',
    );

    expect(copy.code).toBe('PROVIDER_QUOTA_CAP');
    expect(copy.message).toMatch(/configured limit/i);
    // The wrong advice must be gone, not merely deprioritized.
    expect(copy.hint).not.toMatch(/wait a moment/i);
    expect(copy.hint).toMatch(/provider console/i);
  });

  it('never leaves the trigger placeholder as the message the user reads', () => {
    // Verbatim production values for dbos workflow
    // 2daffe85-a0cd-4daa-9cbe-8682c2ca15be BEFORE migration 432: the trigger
    // could not decode the pickled exception, while the backend HAD
    // classified the failure into metadata. Task Center rendered the
    // placeholder because it read only the error_msg half.
    const copy = taskErrorCopy(
      { run_id: '340309387072606', error_code: 'PROVIDER_RATE_LIMIT' },
      'Workflow failed — open detail to see the exception.',
    );

    expect(copy.message).not.toMatch(/open detail/i);
    expect(copy.code).toBe('PROVIDER_RATE_LIMIT');
  });

  it('reads the account cap out of prose when no code was recorded', () => {
    // Row e89739e2-2607-44b0-8bbe-9019a56a1eb5 has an EMPTY metadata
    // error_code (record_ai_error_code did not land that time), so the prose
    // branch is the only thing standing between the user and a raw dump.
    const copy = taskErrorCopy(
      {},
      'DBOSMaxStepRetriesExceeded: all 1 model(s) failed: doubao-seed-2-0-pro-260215 ' +
        '(HTTPStatusError HTTP 429: {"error":{"code":"SetLimitExceeded"}})',
    );

    expect(copy.code).toBeUndefined();
    expect(copy.message).toMatch(/configured limit/i);
  });

  it('falls back to reading the raw text when the backend classified nothing', () => {
    const copy = taskErrorCopy({}, 'HTTP 402 Payment Required');

    expect(copy.code).toBeUndefined();
    expect(copy.message).toMatch(/out of balance/i);
  });

  it('ignores a code this build does not know rather than inventing copy', () => {
    const copy = taskErrorCopy(
      { error_code: 'SOME_FUTURE_CODE' },
      'HTTP 402 Payment Required',
    );

    expect(copy.code).toBeUndefined();
    expect(copy.message).toMatch(/out of balance/i);
  });

  it('uses the translated string when a t() is supplied', () => {
    const copy = taskErrorCopy(
      { error_code: 'PROVIDER_AUTH' },
      null,
      (key) => (key === 'errors.PROVIDER_AUTH.title' ? 'ZH-TITLE' : 'ZH-HINT'),
    );

    expect(copy.message).toBe('ZH-TITLE');
  });

  it('never returns an empty message, even with nothing to go on', () => {
    expect(taskErrorCopy(null, null).message).toBeTruthy();
  });
});
