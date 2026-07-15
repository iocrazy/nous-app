// features/canvas-core/smart/loopVars.test.ts
// Loop batch primitives (Infinite-Canvas parity Phase 1 G3): counter-variable
// injection, round-index expansion and rotating prompt selection — verbatim
// ports of Infinite's smartLoopPrompt / roundIndexes / selectedLocalPrompt
// semantics (smart-canvas.js:12193/14051/12163).

import { describe, expect, it } from 'vitest';
import {
  clampBatchSize,
  clampRoundStart,
  clampRounds,
  injectLoopVariables,
  loopRoundIndexes,
  pickRotatingPrompt,
  sliceLoopImages,
} from './loopVars';

describe('injectLoopVariables', () => {
  it('replaces 《计数》 and [计数] with the round index', () => {
    expect(injectLoopVariables('第《计数》张，again [计数]', { index: 3, total: 5 })).toBe(
      '第3张，again 3',
    );
  });

  it('replaces 《总数》/[总数] and 《进度》/[进度]', () => {
    expect(injectLoopVariables('《计数》/《总数》 progress 《进度》', { index: 2, total: 8 })).toBe(
      '2/8 progress 2/8',
    );
    expect(injectLoopVariables('[进度] of [总数]', { index: 1, total: 4 })).toBe('1/4 of 4');
  });

  it('replaces the canonical {{计数}}/{{总数}}/{{进度}} placeholders', () => {
    expect(
      injectLoopVariables('第 {{计数}} 张 / {{总数}} · {{进度}}', { index: 2, total: 8 }),
    ).toBe('第 2 张 / 8 · 2/8');
  });

  it('trims and passes through text without tokens', () => {
    expect(injectLoopVariables('  plain text  ', { index: 1, total: 1 })).toBe('plain text');
  });
});

describe('loopRoundIndexes', () => {
  it('expands rounds from the start index', () => {
    expect(loopRoundIndexes({ rounds: 3, roundStart: 1 })).toEqual([1, 2, 3]);
    expect(loopRoundIndexes({ rounds: 2, roundStart: 5 })).toEqual([5, 6]);
  });

  it('clamps rounds into 1..100 and start to ≥1', () => {
    expect(loopRoundIndexes({ rounds: 0, roundStart: 0 })).toEqual([1]);
    expect(loopRoundIndexes({ rounds: 1000, roundStart: 1 })).toHaveLength(100);
  });
});

describe('pickRotatingPrompt', () => {
  it('selects (index-1) % length, matching Infinite', () => {
    const prompts = ['a', 'b', 'c'];
    expect(pickRotatingPrompt(prompts, 1)).toBe('a');
    expect(pickRotatingPrompt(prompts, 2)).toBe('b');
    expect(pickRotatingPrompt(prompts, 4)).toBe('a');
  });

  it('skips blank entries and returns empty for an empty list', () => {
    expect(pickRotatingPrompt([], 1)).toBe('');
    expect(pickRotatingPrompt(['  ', ''], 3)).toBe('');
    expect(pickRotatingPrompt([' keep ', ''], 1)).toBe('keep');
  });
});

describe('clamps', () => {
  it('clampRounds bounds into 1..100', () => {
    expect(clampRounds(0)).toBe(1);
    expect(clampRounds(42)).toBe(42);
    expect(clampRounds(500)).toBe(100);
    expect(clampRounds(Number.NaN)).toBe(1);
  });

  it('clampRoundStart bounds to ≥1', () => {
    expect(clampRoundStart(-3)).toBe(1);
    expect(clampRoundStart(7)).toBe(7);
    expect(clampRoundStart(Number.NaN)).toBe(1);
  });
});

describe('clampBatchSize', () => {
  it('clamps to 1..100 and floors', () => {
    expect(clampBatchSize(0)).toBe(1);
    expect(clampBatchSize(3.9)).toBe(3);
    expect(clampBatchSize(500)).toBe(100);
    expect(clampBatchSize(NaN)).toBe(1);
  });
});

describe('sliceLoopImages', () => {
  it('windows by round index (IC slice(N-1, N-1+batch))', () => {
    const imgs = ['a', 'b', 'c', 'd'];
    expect(sliceLoopImages(imgs, 1, 1)).toEqual(['a']);
    expect(sliceLoopImages(imgs, 2, 1)).toEqual(['b']);
    expect(sliceLoopImages(imgs, 1, 2)).toEqual(['a', 'b']);
    expect(sliceLoopImages(imgs, 3, 2)).toEqual(['c', 'd']);
  });

  it('over-run yields empty, empty in yields empty', () => {
    const imgs = ['a', 'b', 'c', 'd'];
    expect(sliceLoopImages(imgs, 9, 1)).toEqual([]);
    expect(sliceLoopImages([], 1, 3)).toEqual([]);
  });
});
