/**
 * wordDiff unit tests — the pure word-level diff engine behind the Cursor-style
 * inline comparison. Covers Latin word granularity, CJK per-character, empty /
 * whole-replacement edges, and loss-less reassembly.
 */
import { describe, expect, it } from 'vitest';
import { wordDiff, type WordSeg } from '../versions/wordDiff';

/** Reassemble one side's text from its segments (must equal the input). */
const text = (segs: WordSeg[]) => segs.map((s) => s.text).join('');

describe('wordDiff', () => {
  it('marks an unchanged string as a single equal segment on both sides', () => {
    const { before, after } = wordDiff('hello world', 'hello world');
    expect(before).toEqual([{ kind: 'equal', text: 'hello world' }]);
    expect(after).toEqual([{ kind: 'equal', text: 'hello world' }]);
  });

  it('diffs at word granularity, keeping the shared words equal', () => {
    const { before, after } = wordDiff('the quick fox', 'the slow fox');
    // Shared "the "/" fox" stay equal; only the middle word changes.
    expect(before.filter((s) => s.kind === 'del').map((s) => s.text)).toEqual(['quick']);
    expect(after.filter((s) => s.kind === 'ins').map((s) => s.text)).toEqual(['slow']);
    expect(text(before)).toBe('the quick fox');
    expect(text(after)).toBe('the slow fox');
  });

  it('does not split a single edited word into letters', () => {
    const { before, after } = wordDiff('color', 'colour');
    // Whole-word replace, not a per-letter 'u' insert.
    expect(before).toEqual([{ kind: 'del', text: 'color' }]);
    expect(after).toEqual([{ kind: 'ins', text: 'colour' }]);
  });

  it('diffs CJK per character', () => {
    const { before, after } = wordDiff('今天天气好', '今天天气坏');
    // Only the final character differs; the prefix stays equal.
    expect(text(before)).toBe('今天天气好');
    expect(text(after)).toBe('今天天气坏');
    expect(before.filter((s) => s.kind === 'del').map((s) => s.text)).toEqual(['好']);
    expect(after.filter((s) => s.kind === 'ins').map((s) => s.text)).toEqual(['坏']);
    expect(before.some((s) => s.kind === 'equal' && s.text === '今天天气')).toBe(true);
  });

  it('handles a pure insertion into empty before', () => {
    const { before, after } = wordDiff('', 'brand new line');
    expect(before).toEqual([]);
    expect(after).toEqual([{ kind: 'ins', text: 'brand new line' }]);
  });

  it('handles a full deletion to empty after', () => {
    const { before, after } = wordDiff('gone entirely', '');
    expect(before).toEqual([{ kind: 'del', text: 'gone entirely' }]);
    expect(after).toEqual([]);
  });

  it('reports a wholesale replacement as one del + one ins', () => {
    const { before, after } = wordDiff('alpha', 'omega');
    expect(before).toEqual([{ kind: 'del', text: 'alpha' }]);
    expect(after).toEqual([{ kind: 'ins', text: 'omega' }]);
  });

  it('reassembles losslessly across mixed CJK + Latin', () => {
    const b = 'Scene 场景 one';
    const a = 'Scene 场景 two';
    const { before, after } = wordDiff(b, a);
    expect(text(before)).toBe(b);
    expect(text(after)).toBe(a);
  });
});
