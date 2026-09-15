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
  // ── C9：截断不再静默丢中段 ────────────────────────────────────────────

  it('截断时保留头尾，并在中间留下一条显式的省略行', () => {
    // 旧行为是 `midA.slice(0, max)`：改动的中段被**整段丢掉**，而尾部的公共
    // 后缀照常拼回去 —— 于是面板读起来像「改动到这里就结束了」。文件头注释
    // 承诺「两侧精确重建」，而这是一次没有任何痕迹的截断。
    const from = Array.from({ length: 6000 }, (_, i) => `a${i}`).join('\n');
    const to = Array.from({ length: 6000 }, (_, i) => `b${i}`).join('\n');
    const r = diffWords(from, to);
    expect(r.truncated).toBe(true);

    const omits = r.segments.filter((s) => s.type === 'omit');
    expect(omits).toHaveLength(1);
    expect(omits[0].omitted).toBeDefined();

    // 头**和**尾都在：旧实现只留得住头。
    expect(renderedText(r, 'from')).toContain('a0');
    expect(renderedText(r, 'from')).toContain('a5999');
    expect(renderedText(r, 'to')).toContain('b0');
    expect(renderedText(r, 'to')).toContain('b5999');
  });

  it('省略的行数是真数出来的，不是估的', () => {
    // 手算的期望值，不是照着实现抄的：maxTokens=4 → 每端留 keep=2 个 token。
    // 'a0\na1\na2\na3\na4\na5' 分词成 11 个 token（词与换行交替），两侧
    // 完全不同所以公共头尾都是 0，中段就是全部 11 个：
    //   头 = tokens[0..2)  = 'a0\n'
    //   丢 = tokens[2..9)  = 'a1\na2\na3\na4'   → 4 行
    //   尾 = tokens[9..11) = '\na5'
    const from = 'a0\na1\na2\na3\na4\na5';
    const to = 'b0\nb1\nb2\nb3\nb4\nb5';
    const r = diffWords(from, to, { maxTokens: 4 });
    expect(r.truncated).toBe(true);
    expect(r.omitted).toEqual({ from: 4, to: 4 });

    // 两端都还在，中间那条说得出丢了多少。
    const shown = renderedText(r, 'from');
    expect(shown).toContain('a0');
    expect(shown).toContain('a5');
    expect(shown).toContain('4 lines omitted');
    // 丢掉的那几行确实不在了 —— 否则「省略」只是句空话。
    expect(shown).not.toContain('a2');
    expect(shown).not.toContain('a3');

    const omit = r.segments.find((s) => s.type === 'omit');
    expect(omit?.omitted).toEqual(r.omitted);
  });

  it('没有截断时既没有省略段，也没有省略行数', () => {
    const r = diffWords('the quick brown fox', 'the quick red fox');
    expect(r.truncated).toBe(false);
    expect(r.segments.some((s) => s.type === 'omit')).toBe(false);
    expect(r.omitted).toEqual({ from: 0, to: 0 });
  });

  it('省略行在两侧都画得出来（它不属于任何一侧）', () => {
    // `Pane` 按 `s.type !== skip` 过滤，`add` 只在 to 侧、`del` 只在 from 侧。
    // 省略标记必须两侧都留下 —— 只画一侧的话，另一侧就又变回静默截断了。
    const from = Array.from({ length: 6000 }, (_, i) => `a${i}`).join('\n');
    const to = Array.from({ length: 6000 }, (_, i) => `b${i}`).join('\n');
    const r = diffWords(from, to);
    expect(renderedText(r, 'from')).toContain('omitted');
    expect(renderedText(r, 'to')).toContain('omitted');
  });
});
