import { describe, it, expect } from 'vitest';
import { isNoteDraft } from './isNoteDraft';

/**
 * Mirror-parity suite: these cases are copied 1:1 from the backend's
 * test_is_note_comment_rule (backend/tests/test_comment_trigger_predicate.py).
 * If the rule changes on either side, change BOTH files in the same PR — a
 * drifted mirror only costs a redundant/missing refetch (the server verdict
 * still rules the chip), but keeping them locked makes the refetch exact.
 */
describe('isNoteDraft', () => {
  const CASES: Array<[string | null | undefined, boolean]> = [
    // canonical: /note followed by a space
    ['/note remember this', true],
    // /note followed by a tab
    ['/note\tremember', true],
    // /note followed by a newline
    ['/note\nremember', true],
    // /note alone (followed by end of string)
    ['/note', true],
    // /note with trailing whitespace only
    ['/note ', true],
    // leading whitespace before /note is ignored
    ['   /note trim me', true],
    // leading newline before /note
    ['\n/note', true],
    // NOT a note: /notex — the token must end at a boundary
    ['/notex is a comment', false],
    ['/noted', false],
    ['/notes', false],
    // NOT a note: /note not at the start (mid-body)
    ['please /note this', false],
    // NOT a note: case matters (verbatim match)
    ['/NOTE loud', false],
    ['/Note titled', false],
    // NOT a note: empty / whitespace / null / undefined
    ['', false],
    ['   ', false],
    [null, false],
    [undefined, false],
    // NOT a note: a bare slash or different command
    ['/', false],
    ['/n', false],
    ['note without slash', false],
  ];

  it.each(CASES)('isNoteDraft(%j) === %s', (body, expected) => {
    expect(isNoteDraft(body)).toBe(expected);
  });
});
