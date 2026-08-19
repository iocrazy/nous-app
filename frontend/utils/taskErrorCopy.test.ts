import { describe, it, expect } from 'vitest';
import { taskErrorCopy } from './taskErrorCopy';

describe('taskErrorCopy', () => {
  it('reproduces the 2026-08-19 ai_summary row: a placeholder error_msg plus a classified code', () => {
    // Both halves are verbatim production values for dbos workflow
    // 2daffe85-a0cd-4daa-9cbe-8682c2ca15be: the trigger could not decode the
    // pickled exception, while the backend HAD classified it correctly into
    // metadata. Task Center showed the placeholder because it never read the
    // metadata half.
    const copy = taskErrorCopy(
      { run_id: '340309387072606', error_code: 'PROVIDER_RATE_LIMIT' },
      'Workflow failed — open detail to see the exception.',
    );

    expect(copy.code).toBe('PROVIDER_RATE_LIMIT');
    expect(copy.message).not.toMatch(/open detail/i);
    expect(copy.message).toMatch(/rate-limiting/i);
    expect(copy.hint).toBeTruthy();
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
