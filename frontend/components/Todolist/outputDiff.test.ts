/**
 * harness 3a §5 — the word-level diff behind the version dialog.
 *
 * The only thing it must never do is lie about which side a word is on: a
 * reader compares two revisions of their own script here, and a mis-attributed
 * line reads as the agent having written something it did not.
 */
import { describe, expect, it } from 'vitest';

import { diffWords, renderedText } from './outputDiff';

const kept = (r: { segments: { type: string; text: string }[] }, type: string): string =>
  r.segments.filter((s) => s.type === type).map((s) => s.text).join('');

describe('diffWords', () => {
  it('two empty sides are one empty diff, not a crash', () => {
    const r = diffWords('', '');
    expect(r.segments).toEqual([]);
    expect(r.added).toBe(0);
    expect(r.removed).toBe(0);
    expect(r.truncated).toBe(false);
  });

  it('everything added when the old side is empty', () => {
    const r = diffWords('', 'a brand new line');
    expect(r.segments.every((s) => s.type === 'add')).toBe(true);
    expect(kept(r, 'add')).toBe('a brand new line');
    expect(r.added).toBe(4);
    expect(r.removed).toBe(0);
  });

  it('everything removed when the new side is empty', () => {
    const r = diffWords('gone for good', '');
    expect(r.segments.every((s) => s.type === 'del')).toBe(true);
    expect(kept(r, 'del')).toBe('gone for good');
    expect(r.removed).toBe(3);
  });

  it('identical text is all same and counts nothing', () => {
    const r = diffWords('the cat sat', 'the cat sat');
    expect(r.segments.map((s) => s.type)).toEqual(['same']);
    expect(r.added).toBe(0);
    expect(r.removed).toBe(0);
  });

  it('a replacement in the middle keeps both ends intact', () => {
    const r = diffWords('the quick brown fox jumps', 'the quick red fox jumps');
    expect(kept(r, 'del')).toBe('brown');
    expect(kept(r, 'add')).toBe('red');
    // both ends survive as context, whitespace included
    expect(renderedText(r, 'from')).toBe('the quick brown fox jumps');
    expect(renderedText(r, 'to')).toBe('the quick red fox jumps');
  });

  it('reconstructs both sides exactly for a multi-line revision', () => {
    const from = 'INT. KITCHEN - DAY\n\nAnna pours coffee.\nShe waits.';
    const to = 'INT. KITCHEN - NIGHT\n\nAnna pours coffee, slowly.\nShe waits.';
    const r = diffWords(from, to);
    expect(renderedText(r, 'from')).toBe(from);
    expect(renderedText(r, 'to')).toBe(to);
  });

  it('truncates an enormous text instead of hanging, and says so', () => {
    const from = Array.from({ length: 40000 }, (_, i) => `w${i}`).join(' ');
    const to = Array.from({ length: 40000 }, (_, i) => `x${i}`).join(' ');
    const started = Date.now();
    const r = diffWords(from, to);
    expect(r.truncated).toBe(true);
    expect(Date.now() - started).toBeLessThan(3000);
  });

  it('an unchanged long head is not paid for twice', () => {
    // The common prefix/suffix comes off before the quadratic part, so a long
    // document with one edited word is cheap and NOT reported as truncated.
    const head = Array.from({ length: 8000 }, (_, i) => `w${i}`).join(' ');
    const r = diffWords(`${head} alpha tail`, `${head} beta tail`);
    expect(r.truncated).toBe(false);
    expect(kept(r, 'del')).toBe('alpha');
    expect(kept(r, 'add')).toBe('beta');
  });
});
